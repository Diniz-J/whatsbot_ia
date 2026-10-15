"""Montagem do payload de envio e a exigencia de credencial."""

import httpx
import pytest

from apps.canal.base import ErroPermanente
from apps.canal.whatsapp import client as modulo_client
from apps.canal.whatsapp.canal import CanalWhatsApp


@pytest.fixture
def capturar(monkeypatch):
    corpos = []

    def post(url, **kwargs):
        corpos.append(kwargs["json"])
        return httpx.Response(status_code=200, json={"messages": [{"id": "wamid.A"}]})

    monkeypatch.setattr(modulo_client.httpx, "post", post)
    return corpos


def test_sem_configuracao_e_erro_explicito():
    """Normalizar entrada nao precisa de credencial, enviar precisa. Falhar
    aqui e melhor que um envio silenciosamente perdido."""
    with pytest.raises(ErroPermanente):
        CanalWhatsApp().enviar_texto("5511999999999", "oi")


def test_configuracao_sem_token_e_erro_explicito(restaurante):
    configuracao = restaurante.configuracao
    configuracao.whatsapp_access_token = ""
    configuracao.save(update_fields=["whatsapp_access_token", "atualizado_em"])

    with pytest.raises(ErroPermanente):
        CanalWhatsApp(configuracao).enviar_texto("5511999999999", "oi")


def test_payload_de_texto(restaurante, capturar):
    configuracao = restaurante.configuracao
    configuracao.whatsapp_access_token = "token"
    configuracao.save(update_fields=["whatsapp_access_token", "atualizado_em"])

    CanalWhatsApp(configuracao).enviar_texto("5511999999999", "mesa reservada")

    assert capturar[0] == {
        "messaging_product": "whatsapp",
        "to": "5511999999999",
        "type": "text",
        "text": {"body": "mesa reservada"},
    }


def test_payload_de_template_com_variaveis(restaurante, capturar):
    configuracao = restaurante.configuracao
    configuracao.whatsapp_access_token = "token"
    configuracao.save(update_fields=["whatsapp_access_token", "atualizado_em"])

    CanalWhatsApp(configuracao).enviar_template(
        "5511999999999", "confirmacao_reserva", "pt_BR", ["20h", "4 pessoas"]
    )

    assert capturar[0]["template"] == {
        "name": "confirmacao_reserva",
        "language": {"code": "pt_BR"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "20h"},
                    {"type": "text", "text": "4 pessoas"},
                ],
            }
        ],
    }


def test_template_sem_variaveis_vai_sem_componentes(restaurante, capturar):
    """Template sem placeholder com `components` vazio e recusado pela Meta."""
    configuracao = restaurante.configuracao
    configuracao.whatsapp_access_token = "token"
    configuracao.save(update_fields=["whatsapp_access_token", "atualizado_em"])

    CanalWhatsApp(configuracao).enviar_template("5511999999999", "boas_vindas", "pt_BR", [])

    assert capturar[0]["template"]["components"] == []
