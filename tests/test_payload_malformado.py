"""Payload assinado mas com formato errado nao pode derrubar o webhook.

O `_valores` diz no proprio docstring que o payload vem de fora e que nao se
confia no formato. Ele defende contra campo ausente e contra lista de lixo,
mas nao contra o campo ser um escalar: `for x in 5` levanta TypeError, e um
TypeError aqui vira 500 num endpoint que o caminho inteiro foi escrito para
responder 403 ou 200.

A assinatura prova que veio da Meta, nao que o corpo faz sentido — e o app
secret e o mesmo em todas as instancias, que e a razao do invariante 3
existir.
"""

import pytest

from apps.canal.whatsapp.canal import CanalWhatsApp, phone_number_ids
from tests.payloads_whatsapp import SEGREDO, corpo_e_assinatura

ESCALARES = (5, 1.5, True, "texto")


@pytest.fixture
def canal():
    return CanalWhatsApp()


@pytest.mark.parametrize("lixo", ESCALARES)
def test_entry_escalar_nao_estoura(canal, lixo):
    payload = {"entry": lixo}

    assert canal.normalizar_evento(payload) == []
    assert canal.normalizar_status(payload) == []
    assert phone_number_ids(payload) == set()


@pytest.mark.parametrize("lixo", ESCALARES)
def test_changes_escalar_nao_estoura(canal, lixo):
    payload = {"entry": [{"changes": lixo}]}

    assert canal.normalizar_evento(payload) == []
    assert canal.normalizar_status(payload) == []
    assert phone_number_ids(payload) == set()


@pytest.mark.parametrize("lixo", ESCALARES)
def test_messages_escalar_nao_estoura(canal, lixo):
    payload = {"entry": [{"changes": [{"value": {"messages": lixo}}]}]}

    assert canal.normalizar_evento(payload) == []


@pytest.mark.parametrize("lixo", ESCALARES)
def test_statuses_escalar_nao_estoura(canal, lixo):
    payload = {"entry": [{"changes": [{"value": {"statuses": lixo}}]}]}

    assert canal.normalizar_status(payload) == []


@pytest.mark.parametrize("lixo", ESCALARES)
def test_contacts_escalar_nao_estoura(canal, lixo):
    payload = {"entry": [{"changes": [{"value": {"messages": [], "contacts": lixo}}]}]}

    assert canal.normalizar_evento(payload) == []


def test_dict_no_lugar_da_lista_nao_estoura(canal):
    """Iterar dict da as chaves, que o `isinstance` descarta — passava por
    acidente. Fica travado para continuar passando por decisao."""
    payload = {"entry": {"changes": "nada"}}

    assert canal.normalizar_evento(payload) == []
    assert phone_number_ids(payload) == set()


@pytest.mark.django_db
def test_webhook_responde_200_e_nao_500(client, restaurante, settings):
    """Mesma resposta de qualquer entrega que nao traz mensagem nem status.

    Nao da para distinguir payload corrompido de tipo de evento que esta
    instancia nao trata, e recusar faria a Meta retentar por dias e desativar
    a subscription.
    """
    settings.WHATSAPP_APP_SECRET = SEGREDO
    corpo, assinatura = corpo_e_assinatura({"entry": 5})

    resposta = client.post(
        "/webhook/whatsapp/",
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura},
    )

    assert resposta.status_code == 200
