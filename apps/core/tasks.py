"""Tarefas do Celery. Todo processamento sai da request e passa por aqui."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.canal.base import ErroPermanente, ErroTransitorio
from apps.canal.fabrica import canal_para
from apps.conversas.models import JANELA_SERVICO, EventoWebhook, Mensagem
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

    Quatro coisas travam sem isto, e as quatro sao perda silenciosa:

    - Mensagem reservada (`enviando`) cujo worker morreu antes da resposta.
      Ninguem mais a reclama, porque o claim justamente impede isso.
    - Mensagem de saida que ficou `enfileirada` sem nunca ser reclamada,
      quando o enfileiramento nao chegou ao broker ou as retentativas de
      `ErroTransitorio` se esgotaram — ver `_reenfileirar_saidas`.
    - Entrega gravada que nunca foi processada, quando o enfileiramento se
      perdeu e a Meta nao reenviou.
    - Mensagem de entrada ja gravada que nunca foi atendida, quando o
      `responder_inbound` nao chegou a ser enfileirado (processo morto entre o
      commit do evento e o `delay`) ou falhou sem retentativa. O evento ja
      consta como processado, entao a varredura de entregas nao a alcanca.
      Essa ultima tem teto de idade na janela de servico — ver
      `_reatender_entradas`.

    O limite de tempo e generoso de proposito: o timeout de envio e de
    segundos, entao uma mensagem em `enviando` por mais de alguns minutos nao
    esta em voo. Mesmo assim, reenviar uma cuja resposta se perdeu pode
    duplicar — e o preco de entrega ao menos uma vez, e e menor que a
    mensagem nunca sair.
    """
    agora = timezone.now()
    reenfileiradas = _destravar_envios(agora)
    paradas = _reenfileirar_saidas(agora)
    reprocessados = _reprocessar_entregas(agora)
    reatendidas = _reatender_entradas(agora)

    if reenfileiradas or paradas or reprocessados or reatendidas:
        logger.warning(
            "Varredura destravou %s envio(s), %s saida(s) parada(s), "
            "%s entrega(s) e %s resposta(s)",
            reenfileiradas,
            paradas,
            reprocessados,
            reatendidas,
        )
    return {
        "envios": reenfileiradas,
        "saidas_paradas": paradas,
        "entregas": reprocessados,
        "respostas": reatendidas,
    }


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


def _reenfileirar_saidas(agora) -> int:
    """Reenfileira saida que ficou `enfileirada` sem nunca ser reclamada.

    O `_destravar_envios` acima nao alcanca este caso: ele procura o que foi
    reservado (`enviando`) e nunca terminou. Tres caminhos deixam a mensagem
    gravada e parada antes disso, e nos tres a resposta simplesmente nunca sai:

    - o `delay` do `on_commit` nao chegou ao broker (Redis fora do ar);
    - o worker morreu entre receber a tarefa e reservar a mensagem;
    - o `enviar_outbound` esgotou as retentativas de `ErroTransitorio`, que
      devolvem o status para `enfileirada` antes de relancar.

    Teto de idade na janela de servico, pelo mesmo motivo do
    `_reatender_entradas`: passada ela a Meta recusa texto livre, entao
    insistir nao entrega nada.

    Template fica de fora: o idioma e as variaveis vao como argumento da
    tarefa e nao estao gravados na mensagem, entao reenfileirar um mandaria
    template sem os dados que ele preenche. Enquanto nao estiverem na linha,
    recuperar template as cegas e pior que deixar aparecer como pendente.
    """
    limite = agora - timedelta(minutes=settings.ENVIO_TRAVADO_MINUTOS)
    paradas = list(
        Mensagem.objects.filter(
            direcao=Mensagem.Direcao.SAIDA,
            status=Mensagem.Status.ENFILEIRADA,
            template="",
            atualizado_em__lt=limite,
            criado_em__gte=agora - JANELA_SERVICO,
        ).values_list("pk", flat=True)
    )
    for mensagem_id in paradas:
        enviar_outbound.delay(mensagem_id)
    return len(paradas)


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


def _reatender_entradas(agora) -> int:
    """Reenfileira o atendimento de entradas que nunca foram respondidas.

    `respondida_em` nula significa que nenhum `responder_inbound` chegou a
    reservar a mensagem: o `_reservar` do pipeline carimba esse campo com lock
    antes de redigir. Reenfileirar e seguro porque esse mesmo lock impede que
    duas execucoes atendam a mesma entrada.

    Tem teto de idade, e nao so piso, porque sem ele a insistencia e eterna: o
    `responder_mensagem` roda dentro de `transaction.atomic`, entao um defeito
    que estoure excecao no atendimento desfaz o carimbo no rollback, a mensagem
    volta a ser nula e a varredura a escolhe de novo a cada volta — no tier de
    IA, gastando token em cada uma. A janela de servico e o teto natural:
    passada ela o `agendar_texto` recusaria o envio com `JanelaFechada`, entao
    insistir nao entrega nada.

    O teto olha a idade da propria mensagem, enquanto a janela e ancorada na
    ultima entrada da conversa. Uma mensagem nova do cliente reabre a janela,
    mas responder a pergunta de ontem nao e o que se quer: a nova tem a
    resposta dela.
    """
    limite = agora - timedelta(minutes=settings.RESPOSTA_TRAVADA_MINUTOS)
    pendentes = list(
        Mensagem.objects.filter(
            direcao=Mensagem.Direcao.ENTRADA,
            respondida_em__isnull=True,
            criado_em__lt=limite,
            criado_em__gte=agora - JANELA_SERVICO,
        ).values_list("pk", flat=True)
    )
    for mensagem_id in pendentes:
        responder_inbound.delay(mensagem_id)
    return len(pendentes)
