"""Handshake de verificacao do webhook (o GET que a Meta faz uma vez)."""

import pytest

URL = "/webhook/whatsapp/"


@pytest.fixture(autouse=True)
def token(settings):
    settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN = "token-combinado"


def test_devolve_o_desafio_com_token_correto(client):
    resposta = client.get(
        URL,
        {"hub.mode": "subscribe", "hub.verify_token": "token-combinado", "hub.challenge": "42"},
    )

    assert resposta.status_code == 200
    assert resposta.content == b"42"


def test_recusa_token_errado(client):
    resposta = client.get(
        URL,
        {"hub.mode": "subscribe", "hub.verify_token": "chute", "hub.challenge": "42"},
    )

    assert resposta.status_code == 403


def test_recusa_quando_nenhum_token_esta_configurado(client, settings):
    """Sem token configurado, qualquer um assinaria o webhook. Falha fechada."""
    settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN = ""

    resposta = client.get(
        URL, {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "42"}
    )

    assert resposta.status_code == 403
