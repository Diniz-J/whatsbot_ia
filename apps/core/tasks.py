"""Tarefas do Celery. Todo processamento sai da request e passa por aqui."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.canal.base import ErroPermanente, ErroTransitorio
from apps.canal.fabrica import canal_para
from apps.conversas.models import EventoWebhook, Mensagem
from apps.core.pipeline import responder_mensagem
from apps.core.services import processar_evento

logger = logging.getLogger(__name__)


@shared_task(name="core.processar_inbound")
def processar_inbound(evento_id: int) -> int:
    """Normaliza uma entrega ja gravada e devolve quantas mensagens criou.

    Recebe o id e nao o payload: o evento ja esta no banco quando a tarefa e
    enfileirada, e passar o id mantem a mensagem da fila pequena e faz o
    reprocessamento ler sempre o estado atual.
    """
    try:
        evento = EventoWebhook.objects.get(pk=evento_id)
    except EventoWebhook.DoesNotExist:
        # Nao reenfileira: evento apagado nao volta a existir.
        logger.error("Evento de webhook %s nao existe mais", evento_id)
        return 0

    criadas = processar_evento(evento)
    # Tarefa propria por mensagem: uma falha ao redigir nao desfaz a
    # normalizacao, e cada uma retenta por conta.
    for mensagem in criadas:
        responder_inbound.delay(mensagem.pk)
    return len(criadas)


@shared_task(name="core.responder_inbound")
def responder_inbound(mensagem_id: int) -> int | None:
    """Atende uma mensagem de entrada e devolve o id da resposta agendada."""
    resposta = responder_mensagem(mensagem_id)
    return resposta.pk if resposta is not None else None


@shared_task(
    bind=True,
    name="core.enviar_outbound",
    autoretry_for=(ErroTransitorio,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def enviar_outbound(self, mensagem_id: int, idioma: str = "", variaveis=None) -> str | None:
    """Entrega uma mensagem ja gravada ao canal e devolve o id da Meta.

    Retenta so `ErroTransitorio`: repetir um numero invalido ou um template
    reprovado queima cota e nunca passa, e nao repetir um 503 perde a
    mensagem.
    """
    mensagem = _reservar(mensagem_id)
    if mensagem is None:
        return None

    try:
        # Fora de transacao de proposito: a chamada de rede nao pode segurar
        # lock de linha, e o claim acima ja garantiu exclusividade.
        id_externo = _entregar(mensagem, idioma, variaveis or [])
    except ErroTransitorio:
        # Volta para a fila de verdade: o status destravado deixa a proxima
        # tentativa reservar de novo.
        Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.ENFILEIRADA)
        raise
    except ErroPermanente as erro:
        logger.error("Envio da mensagem %s recusado em definitivo: %s", mensagem.pk, erro)
        Mensagem.objects.filter(pk=mensagem.pk).update(
            status=Mensagem.Status.FALHOU, erro=str(erro)
        )
        return None

    Mensagem.objects.filter(pk=mensagem.pk).update(
        status=Mensagem.Status.ENVIADA, id_externo=id_externo, erro=""
    )
    return id_externo


def _reservar(mensagem_id: int) -> Mensagem | None:
    """Pega a mensagem para si, ou desiste se outra execucao ja pegou."""
    with transaction.atomic():
        try:
            mensagem = (
                Mensagem.objects.select_for_update()
                .select_related("conversa", "conversa__contato")
                .get(pk=mensagem_id)
            )
        except Mensagem.DoesNotExist:
            logger.error("Mensagem %s nao existe mais", mensagem_id)
            return None

        if mensagem.status != Mensagem.Status.ENFILEIRADA:
            logger.info("Mensagem %s esta em %s, nada a enviar", mensagem_id, mensagem.status)
            return None

        mensagem.status = Mensagem.Status.ENVIANDO
        mensagem.save(update_fields=["status", "atualizado_em"])
        return mensagem


def _entregar(mensagem: Mensagem, idioma: str, variaveis: list[str]) -> str:
    configuracao = _configuracao(mensagem)
    canal = canal_para(configuracao)
    telefone = mensagem.conversa.contato.telefone

    if mensagem.template:
        return canal.enviar_template(telefone, mensagem.template, idioma or "pt_BR", variaveis)
    return canal.enviar_texto(telefone, mensagem.texto)


def _configuracao(mensagem: Mensagem):
    from apps.tenants.models import Configuracao

    try:
        return Configuracao.objects.get(tenant_id=mensagem.tenant_id)
    except Configuracao.DoesNotExist as erro:
        # Permanente: sem configuracao nao ha para onde mandar, e repetir nao
        # faz a configuracao aparecer.
        raise ErroPermanente(f"cliente {mensagem.tenant_id} sem configuracao") from erro


@shared_task(name="core.varrer_pendentes")
def varrer_pendentes() -> dict[str, int]:
    """Recupera o que ficou preso num estado nao-final.

    Duas coisas travam sem isto, e as duas sao perda silenciosa:

    - Mensagem reservada (`enviando`) cujo worker morreu antes da resposta.
      Ninguem mais a reclama, porque o claim justamente impede isso.
    - Entrega gravada que nunca foi processada, quando o enfileiramento se
      perdeu e a Meta nao reenviou.

    O limite de tempo e generoso de proposito: o timeout de envio e de
    segundos, entao uma mensagem em `enviando` por mais de alguns minutos nao
    esta em voo. Mesmo assim, reenviar uma cuja resposta se perdeu pode
    duplicar — e o preco de entrega ao menos uma vez, e e menor que a
    mensagem nunca sair.
    """
    agora = timezone.now()
    reenfileiradas = _destravar_envios(agora)
    reprocessados = _reprocessar_entregas(agora)

    if reenfileiradas or reprocessados:
        logger.warning(
            "Varredura destravou %s envio(s) e %s entrega(s)", reenfileiradas, reprocessados
        )
    return {"envios": reenfileiradas, "entregas": reprocessados}


def _destravar_envios(agora) -> int:
    limite = agora - timedelta(minutes=settings.ENVIO_TRAVADO_MINUTOS)
    travadas = list(
        Mensagem.objects.filter(
            status=Mensagem.Status.ENVIANDO, atualizado_em__lt=limite
        ).values_list("pk", flat=True)
    )
    if not travadas:
        return 0

    Mensagem.objects.filter(pk__in=travadas).update(status=Mensagem.Status.ENFILEIRADA)
    for mensagem_id in travadas:
        enviar_outbound.delay(mensagem_id)
    return len(travadas)


def _reprocessar_entregas(agora) -> int:
    limite = agora - timedelta(minutes=settings.INBOUND_TRAVADO_MINUTOS)
    pendentes = list(
        EventoWebhook.objects.filter(processado_em__isnull=True, criado_em__lt=limite).values_list(
            "pk", flat=True
        )
    )
    for evento_id in pendentes:
        processar_inbound.delay(evento_id)
    return len(pendentes)
