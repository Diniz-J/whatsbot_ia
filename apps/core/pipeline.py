"""Do que o cliente mandou ate a resposta agendada.

E o unico lugar que conhece as duas pontas: pega a mensagem de entrada ja
normalizada, pergunta ao atendimento do tier do cliente, e agenda o envio.
O atendimento nao sabe que existe canal; o canal nao sabe que existe
atendimento.
"""

import logging

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.conversas.models import ENTREGUES_AO_CLIENTE, JANELA_SERVICO, Mensagem
from apps.core.envio import JanelaFechada, agendar_texto
from apps.responder.base import Autor, Contexto, Pergunta, Turno
from apps.responder.registry import responder_para
from apps.tenants.models import Configuracao

logger = logging.getLogger(__name__)


def responder_mensagem(mensagem_id: int) -> Mensagem | None:
    """Responde uma mensagem de entrada. Devolve a resposta agendada, ou None.

    Idempotente: `respondida_em` e lido com lock, porque a fila pode
    reentregar a tarefa e responder duas vezes seria visivel para o cliente
    do cliente — e cobrado, se virar template.
    """
    with transaction.atomic():
        mensagem = _reservar(mensagem_id)
        if mensagem is None:
            return None

        configuracao = _configuracao(mensagem.tenant_id)
        if configuracao is None:
            logger.error("Cliente %s sem configuracao: nao da para atender", mensagem.tenant_id)
            return None

        resposta = responder_para(configuracao).handle(
            Pergunta(texto=mensagem.texto, tipo=mensagem.tipo),
            Contexto(
                tenant_id=mensagem.tenant_id,
                nome_contato=mensagem.conversa.contato.nome_perfil,
                historico=_historico(mensagem),
                contato_id=mensagem.conversa.contato_id,
            ),
        )
        if resposta is None:
            logger.info("Atendimento optou por nao responder a mensagem %s", mensagem_id)
            return None

        try:
            return agendar_texto(mensagem.conversa, resposta.texto, uso=resposta.uso)
        except JanelaFechada:
            # A entrada do cliente abre a janela, entao isto so acontece se a
            # mensagem ficou mais de 24h na fila. Responder por template
            # custaria dinheiro para entregar uma resposta velha.
            logger.warning(
                "Mensagem %s saiu da janela antes de ser respondida; sem resposta", mensagem_id
            )
            return None


def _reservar(mensagem_id: int) -> Mensagem | None:
    try:
        mensagem = (
            Mensagem.objects.select_for_update()
            .select_related("conversa", "conversa__contato")
            .get(pk=mensagem_id)
        )
    except Mensagem.DoesNotExist:
        logger.error("Mensagem %s nao existe mais", mensagem_id)
        return None

    if mensagem.direcao != Mensagem.Direcao.ENTRADA:
        logger.error("Mensagem %s nao e de entrada: nada a responder", mensagem_id)
        return None
    if mensagem.respondida_em is not None:
        logger.info("Mensagem %s ja foi respondida", mensagem_id)
        return None

    mensagem.respondida_em = timezone.now()
    mensagem.save(update_fields=["respondida_em", "atualizado_em"])
    return mensagem


def _historico(mensagem: Mensagem) -> tuple[Turno, ...]:
    """As falas anteriores desta conversa, da mais antiga para a mais nova.

    Dois limites, porque cada um sozinho falha: a janela de servico impede que
    a conversa da semana passada volte, e o teto de turnos impede que um dia
    movimentado vire prompt gigante. A janela e ancorada na mensagem que esta
    sendo respondida, e nao em `agora`: o fio que importa e o dela.

    Saida entra somente nos status que chegaram ao cliente. Incluir uma que
    falhou faria o atendimento acreditar que disse algo que o cliente nunca
    leu, e responder em cima disso.

    Mensagem sem texto (template puro, audio, imagem) fica fora: nao ha fala
    para reproduzir, e bloco de texto vazio a API recusa.

    O corte pega as mais recentes e so depois reordena, para o teto ficar com
    o fim da conversa e nao com o comeco dela.
    """
    anteriores = (
        Mensagem.objects.filter(
            conversa_id=mensagem.conversa_id,
            criado_em__lt=mensagem.criado_em,
            criado_em__gte=mensagem.criado_em - JANELA_SERVICO,
        )
        .exclude(texto="")
        .filter(
            models.Q(direcao=Mensagem.Direcao.ENTRADA)
            | models.Q(direcao=Mensagem.Direcao.SAIDA, status__in=ENTREGUES_AO_CLIENTE)
        )
        .order_by("-criado_em")
        .values_list("direcao", "texto")[: settings.HISTORICO_MAX_TURNOS]
    )
    return tuple(
        Turno(
            autor=Autor.CLIENTE if direcao == Mensagem.Direcao.ENTRADA else Autor.ATENDENTE,
            texto=texto,
        )
        for direcao, texto in reversed(list(anteriores))
    )


def _configuracao(tenant_id: int) -> Configuracao | None:
    return Configuracao.objects.filter(tenant_id=tenant_id).first()
