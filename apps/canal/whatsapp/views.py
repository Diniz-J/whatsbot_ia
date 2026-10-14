"""Webhook da WhatsApp Cloud API.

Faz o minimo na request e devolve 200 rapido: a Meta reenvia a entrega quando
demora, e o reenvio custa trabalho dobrado. Conferir, gravar cru, enfileirar.
"""

import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.canal.whatsapp.assinatura import assinatura_valida
from apps.canal.whatsapp.canal import CanalWhatsApp, phone_number_ids
from apps.conversas.models import EventoWebhook
from apps.core.tasks import processar_inbound
from apps.tenants.models import Configuracao

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    if request.method == "GET":
        return _verificar(request)
    return _receber(request)


def _verificar(request):
    """Handshake de assinatura do webhook, feito uma vez pela Meta.

    Ela chama com um token combinado e espera o `hub.challenge` de volta em
    texto puro.
    """
    if request.GET.get("hub.mode") != "subscribe":
        return HttpResponseForbidden("modo invalido")

    token = request.GET.get("hub.verify_token")
    esperado = settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
    if not esperado or token != esperado:
        logger.warning("Verificacao de webhook recusada: token invalido")
        return HttpResponseForbidden("token invalido")

    desafio = request.GET.get("hub.challenge")
    if not desafio:
        return HttpResponseBadRequest("challenge ausente")
    return HttpResponse(desafio, content_type="text/plain")


def _receber(request):
    """Recebe uma entrega: confere, grava cru, enfileira."""
    if not assinatura_valida(
        request.body,
        request.headers.get("X-Hub-Signature-256"),
        settings.WHATSAPP_APP_SECRET,
    ):
        logger.warning("Entrega recusada: assinatura invalida")
        return HttpResponseForbidden("assinatura invalida")

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("Entrega recusada: corpo nao e JSON valido")
        return HttpResponseBadRequest("json invalido")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("json invalido")

    configuracao = _configuracao_do_destinatario(payload)
    if configuracao is None:
        # Invariante 3: a assinatura prova que veio da Meta, nao que era para
        # este cliente. O app secret e o mesmo em todas as instancias, entao
        # sem esta conferencia quem soubesse a URL injetaria conversa de
        # outro numero.
        logger.warning("Entrega recusada: numero nao pertence a esta instancia")
        return HttpResponseForbidden("destinatario desconhecido")

    _gravar_e_enfileirar(configuracao.tenant_id, payload)
    return HttpResponse(status=200)


def _configuracao_do_destinatario(payload: dict) -> Configuracao | None:
    """Casa o numero da entrega com o cliente desta instancia.

    Entrega com mais de um numero, ou com numero que nao e o configurado, nao
    e desta instancia: uma instancia atende um cliente (DESIGN.md secao 8).
    """
    numeros = phone_number_ids(payload)
    if len(numeros) != 1:
        return None
    return (
        Configuracao.objects.filter(whatsapp_phone_number_id=numeros.pop())
        .select_related("tenant")
        .first()
    )


def _gravar_e_enfileirar(tenant_id: int, payload: dict) -> None:
    """Grava cada mensagem da entrega e enfileira o processamento.

    O `get_or_create` no id da mensagem e o que torna o reenvio da Meta um
    no-op (invariante 4). O enfileiramento vai em `on_commit` para o worker
    nunca procurar uma linha que a transacao ainda nao confirmou.
    """
    with transaction.atomic():
        for entrada in CanalWhatsApp().normalizar_evento(payload):
            evento, criado = EventoWebhook.objects.get_or_create(
                tenant_id=tenant_id,
                id_mensagem=entrada.id_externo,
                defaults={"payload": payload},
            )
            if not criado:
                logger.info("Entrega repetida ignorada: %s", entrada.id_externo)
                continue
            transaction.on_commit(lambda evento_id=evento.pk: processar_inbound.delay(evento_id))
