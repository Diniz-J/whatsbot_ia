"""O que o webhook recusa, e com que codigo.

Cada caso aqui ja levou 500 ou 403 indevido em algum momento: sao os erros
que um endpoint publico e sem autenticacao nao pode cometer.
"""

import pytest

from apps.conversas.models import EventoWebhook, Mensagem
from tests.payloads_whatsapp import (
    SEGREDO,
    corpo_e_assinatura,
    payload_mensagem,
    payload_status_de_template,
)

URL = "/webhook/whatsapp/"


@pytest.fixture(autouse=True)
def credenciais(settings):
    settings.WHATSAPP_APP_SECRET = SEGREDO
    settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN = "token-combinado"


@pytest.mark.parametrize(
    "assinatura",
    [
        # Caractere nao-ASCII: `compare_digest` sobre str levantaria TypeError
        # e o 403 viraria 500 num endpoint sem autenticacao.
        "sha256=é" + "a" * 63,
        "sha256=" + "a" * 10,
        "sha256=" + "z" * 64,
        "sha256=",
        "md5=" + "a" * 64,
        "",
    ],
)
def test_assinatura_malformada_e_403_e_nao_500(client, restaurante, assinatura):
    corpo, _ = corpo_e_assinatura(payload_mensagem())

    resposta = client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura},
    )

    assert resposta.status_code == 403
    assert not EventoWebhook.objects.exists()


def test_token_de_verificacao_nao_ascii_e_403_e_nao_500(client):
    resposta = client.get(
        URL,
        {"hub.mode": "subscribe", "hub.verify_token": "tokén", "hub.challenge": "42"},
    )

    assert resposta.status_code == 403


def test_entrega_sem_mensagem_e_aceita(client, restaurante):
    """Aprovacao de template chega nesta URL e nao tem `metadata`.

    Recusar faria a Meta retentar por dias e desativar a subscription.
    """
    corpo, assinatura = corpo_e_assinatura(payload_status_de_template())

    resposta = client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura},
    )

    assert resposta.status_code == 200
    assert not EventoWebhook.objects.exists()
    assert not Mensagem.objects.exists()


def test_cliente_inativo_e_recusado(client, restaurante):
    """Desativar o cliente para o atendimento; o historico fica no banco."""
    restaurante.ativo = False
    restaurante.save(update_fields=["ativo", "atualizado_em"])
    corpo, assinatura = corpo_e_assinatura(payload_mensagem())

    resposta = client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura},
    )

    assert resposta.status_code == 403
    assert not EventoWebhook.objects.exists()
