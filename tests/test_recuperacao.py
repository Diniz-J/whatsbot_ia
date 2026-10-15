"""Entrega gravada que nunca foi processada precisa de uma segunda chance.

Se o broker estiver fora no `on_commit`, ou o worker morrer antes de rodar, o
evento fica no banco com `processado_em` nulo. A reentrega da Meta e a unica
chance de recuperar, e descartar ela silenciosamente perderia a mensagem.
"""

import pytest

from apps.conversas.models import EventoWebhook, Mensagem
from tests.payloads_whatsapp import SEGREDO, corpo_e_assinatura, payload_mensagem

URL = "/webhook/whatsapp/"


@pytest.fixture(autouse=True)
def segredo(settings):
    settings.WHATSAPP_APP_SECRET = SEGREDO


def entregar(client, payload):
    corpo, assinatura = corpo_e_assinatura(payload)
    return client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura},
    )


def test_reentrega_de_evento_nao_processado_volta_para_a_fila(
    client, restaurante, django_capture_on_commit_callbacks
):
    payload = payload_mensagem()
    # Simula o enfileiramento perdido: evento gravado, nunca processado.
    EventoWebhook.objects.create(tenant=restaurante, id_mensagem="wamid.AAA", payload=payload)

    with django_capture_on_commit_callbacks(execute=True):
        assert entregar(client, payload).status_code == 200

    evento = EventoWebhook.objects.get()
    assert evento.processado_em is not None
    assert Mensagem.objects.count() == 1


def test_reentrega_de_evento_ja_processado_nao_refaz_nada(
    client, restaurante, django_capture_on_commit_callbacks
):
    payload = payload_mensagem()
    with django_capture_on_commit_callbacks(execute=True):
        entregar(client, payload)
    processado_em = EventoWebhook.objects.get().processado_em

    with django_capture_on_commit_callbacks(execute=True):
        entregar(client, payload)

    assert EventoWebhook.objects.get().processado_em == processado_em
    assert Mensagem.objects.count() == 1
