"""Tarefas do Celery. Todo processamento sai da request e passa por aqui."""

import logging

from celery import shared_task

from apps.conversas.models import EventoWebhook
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

    return len(processar_evento(evento))
