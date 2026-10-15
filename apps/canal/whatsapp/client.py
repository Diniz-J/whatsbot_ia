"""Cliente HTTP da Graph API, so o necessario para enviar mensagem.

Traduz resposta de rede em `ErroTransitorio` ou `ErroPermanente`, que e a
unica coisa que a fila precisa saber para decidir se retenta.
"""

import logging

import httpx

from apps.canal.base import ErroPermanente, ErroTransitorio

logger = logging.getLogger(__name__)

BASE = "https://graph.facebook.com"
# Codigos que melhoram sozinhos: fila da Meta cheia, instabilidade do lado
# dela. O resto do 4xx e problema do nosso payload e nao passa por repeticao.
TRANSITORIOS = {408, 429, 500, 502, 503, 504}


class ClienteGraph:
    def __init__(self, phone_number_id: str, token: str, versao: str, timeout: float):
        self._phone_number_id = phone_number_id
        self._token = token
        self._versao = versao
        self._timeout = timeout

    def enviar(self, corpo: dict) -> str:
        """Posta a mensagem e devolve o id que a Meta atribuiu."""
        url = f"{BASE}/{self._versao}/{self._phone_number_id}/messages"
        try:
            resposta = httpx.post(
                url,
                json=corpo,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as erro:
            raise ErroTransitorio("timeout falando com a Graph API") from erro
        except httpx.HTTPError as erro:
            # Erro de transporte: DNS, conexao recusada, TLS. Nenhum deles diz
            # que a mensagem nao foi aceita, entao vale retentar.
            raise ErroTransitorio(f"falha de rede na Graph API: {erro}") from erro

        if resposta.status_code in TRANSITORIOS:
            raise ErroTransitorio(
                f"Graph API devolveu {resposta.status_code}: {self._motivo(resposta)}"
            )
        if resposta.status_code >= 400:
            raise ErroPermanente(
                f"Graph API recusou com {resposta.status_code}: {self._motivo(resposta)}"
            )

        return self._id_da_mensagem(resposta)

    @staticmethod
    def _motivo(resposta: httpx.Response) -> str:
        """Mensagem de erro da Meta, sem arrastar o corpo inteiro para o log.

        Nunca inclui cabecalho: o token vai no Authorization e nao pode
        aparecer em log nem em Sentry.
        """
        try:
            erro = resposta.json().get("error") or {}
        except ValueError:
            return "resposta sem json"
        return f"{erro.get('code')} {erro.get('message')}".strip()

    @staticmethod
    def _id_da_mensagem(resposta: httpx.Response) -> str:
        """O id vem em `messages[0].id`.

        Resposta 200 sem id e tratada como transitoria: a mensagem pode ter
        sido aceita, e sem id nao ha como casar o status depois — melhor
        retentar e arriscar duplicar do que perder o rastro.
        """
        try:
            mensagens = resposta.json()["messages"]
            return str(mensagens[0]["id"])
        except (ValueError, KeyError, IndexError, TypeError) as erro:
            raise ErroTransitorio("Graph API respondeu 200 sem id de mensagem") from erro
