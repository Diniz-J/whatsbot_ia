"""Traducao de resposta HTTP em erro que a fila entende.

A classificacao e o que decide entre retentar e desistir, e errar nos dois
sentidos custa: repetir um numero invalido queima cota, desistir de um 503
perde a mensagem.
"""

import httpx
import pytest

from apps.canal.base import ErroPermanente, ErroTransitorio
from apps.canal.whatsapp import client as modulo_client
from apps.canal.whatsapp.client import ClienteGraph

TOKEN = "token-secreto-do-cliente"


def cliente():
    return ClienteGraph(phone_number_id="111", token=TOKEN, versao="v21.0", timeout=5.0)


def responder(monkeypatch, status, json=None, texto=None):
    def post(*args, **kwargs):
        if texto is not None:
            return httpx.Response(status_code=status, text=texto)
        return httpx.Response(status_code=status, json=json or {})

    monkeypatch.setattr(modulo_client.httpx, "post", post)


def test_sucesso_devolve_o_id_da_meta(monkeypatch):
    responder(monkeypatch, 200, {"messages": [{"id": "wamid.ZZZ"}]})

    assert cliente().enviar({}) == "wamid.ZZZ"


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_status_transitorio_pede_retentativa(monkeypatch, status):
    responder(monkeypatch, status, {"error": {"code": 1, "message": "tente depois"}})

    with pytest.raises(ErroTransitorio):
        cliente().enviar({})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_status_permanente_nao_pede_retentativa(monkeypatch, status):
    responder(monkeypatch, status, {"error": {"code": 131026, "message": "undeliverable"}})

    with pytest.raises(ErroPermanente):
        cliente().enviar({})


def test_timeout_e_transitorio(monkeypatch):
    def post(*args, **kwargs):
        raise httpx.ReadTimeout("estourou")

    monkeypatch.setattr(modulo_client.httpx, "post", post)

    with pytest.raises(ErroTransitorio):
        cliente().enviar({})


def test_erro_de_rede_e_transitorio(monkeypatch):
    """Conexao recusada nao diz que a mensagem foi rejeitada."""

    def post(*args, **kwargs):
        raise httpx.ConnectError("sem rota")

    monkeypatch.setattr(modulo_client.httpx, "post", post)

    with pytest.raises(ErroTransitorio):
        cliente().enviar({})


def test_200_sem_id_e_transitorio(monkeypatch):
    """Sem id nao ha como casar o status depois: melhor retentar e arriscar
    duplicar do que perder o rastro da mensagem."""
    responder(monkeypatch, 200, {"messages": []})

    with pytest.raises(ErroTransitorio):
        cliente().enviar({})


def test_corpo_que_nao_e_json_nao_explode(monkeypatch):
    responder(monkeypatch, 500, texto="<html>bad gateway</html>")

    with pytest.raises(ErroTransitorio):
        cliente().enviar({})


def test_token_nunca_aparece_na_mensagem_de_erro(monkeypatch):
    """O erro vai para log e para o Sentry; o token do cliente nao pode ir."""
    responder(monkeypatch, 401, {"error": {"code": 190, "message": "token invalido"}})

    with pytest.raises(ErroPermanente) as erro:
        cliente().enviar({})

    assert TOKEN not in str(erro.value)


def test_url_usa_versao_e_numero_do_cliente(monkeypatch):
    chamadas = []

    def post(url, **kwargs):
        chamadas.append((url, kwargs["headers"]))
        return httpx.Response(status_code=200, json={"messages": [{"id": "wamid.A"}]})

    monkeypatch.setattr(modulo_client.httpx, "post", post)
    cliente().enviar({})

    url, headers = chamadas[0]
    assert url == "https://graph.facebook.com/v21.0/111/messages"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
