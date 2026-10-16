"""Do que o cliente mandou ate a resposta agendada.

E o unico lugar que conhece as duas pontas: pega a mensagem de entrada ja
normalizada, pergunta ao atendimento do tier do cliente, e agenda o envio.
O atendimento nao sabe que existe canal; o canal nao sabe que existe
atendimento.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.conversas.models import Mensagem
from apps.core.envio import JanelaFechada, agendar_texto
from apps.responder.base import Contexto, Pergunta
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


def _configuracao(tenant_id: int) -> Configuracao | None:
    return Configuracao.objects.filter(tenant_id=tenant_id).first()
