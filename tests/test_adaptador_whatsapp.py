"""Traducao do payload da Cloud API para o vocabulario do dominio.

Testes puros: o adaptador nao toca em banco nem em HTTP.
"""

import pytest

from apps.canal.whatsapp.canal import CanalWhatsApp, phone_number_ids
from tests.payloads_whatsapp import payload_mensagem, payload_status


def test_extrai_uma_mensagem_de_texto():
    (entrada,) = CanalWhatsApp().normalizar_evento(payload_mensagem())

    assert entrada.id_externo == "wamid.AAA"
    assert entrada.telefone == "5511999999999"
    assert entrada.nome_perfil == "Ze"
    assert entrada.tipo == "text"
    assert entrada.texto == "oi, qual o horario de voces?"


def test_mensagem_nao_textual_vem_com_tipo_e_sem_texto():
    (entrada,) = CanalWhatsApp().normalizar_evento(payload_mensagem(tipo="audio"))

    assert entrada.tipo == "audio"
    assert entrada.texto == ""


def test_status_de_envio_nao_e_mensagem():
    """Status chega na mesma URL e pertence ao lado de saida."""
    assert CanalWhatsApp().normalizar_evento(payload_status()) == []


def test_ignora_mensagem_sem_id():
    """O id e a chave de idempotencia; sem ele a mensagem e inutilizavel."""
    payload = payload_mensagem()
    del payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]

    assert CanalWhatsApp().normalizar_evento(payload) == []


def test_le_o_numero_de_destino():
    assert phone_number_ids(payload_mensagem(phone_number_id="123")) == {"123"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"entry": None},
        {"entry": ["nao e dicionario"]},
        {"entry": [{"changes": None}]},
        {"entry": [{"changes": [{"value": "nao e dicionario"}]}]},
        {"entry": [{"changes": [{"value": {"messages": ["nao e dicionario"]}}]}]},
        {"entry": [{"changes": [{"value": {"metadata": "nao e dicionario"}}]}]},
    ],
)
def test_payload_torto_nao_explode(payload):
    """O payload vem de fora: formato inesperado e dado invalido, nao erro 500."""
    assert CanalWhatsApp().normalizar_evento(payload) == []
    assert phone_number_ids(payload) == set()
