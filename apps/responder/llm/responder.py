"""O tier hibrido: o modelo entende a pergunta, o dado vem do catalogo."""

import logging

from apps.responder.base import Contexto, Pergunta, Responder, Resposta
from apps.responder.llm.cliente import ErroLLM, conversar
from apps.responder.llm.prompt import sistema
from apps.responder.rules import RuleResponder

logger = logging.getLogger(__name__)


class LLMResponder(Responder):
    """Entende em linguagem natural; afirma somente o que a ferramenta disse.

    Cai no `RuleResponder` quando o modelo falha. Atender pior e melhor que
    nao atender: o cliente final nao tem culpa de a API estar fora, e o dono
    do negocio paga por um atendimento que funciona.
    """

    def __init__(self, nome_negocio: str = ""):
        self._nome_negocio = nome_negocio

    def handle(self, pergunta: Pergunta, contexto: Contexto) -> Resposta | None:
        if pergunta.tipo != "text" or not pergunta.texto.strip():
            # Audio e imagem o modelo nao recebe nesta fatia; a regra tem a
            # resposta certa, que e oferecer uma pessoa.
            return RuleResponder().handle(pergunta, contexto)

        try:
            resposta = conversar(
                sistema=sistema(self._nome_negocio),
                pergunta=pergunta.texto,
                tenant_id=contexto.tenant_id,
            )
        except ErroLLM as erro:
            logger.exception("Atendimento por IA falhou, caindo na regra: %s", erro)
            return RuleResponder().handle(pergunta, contexto)

        return Resposta(texto=resposta.texto, uso=resposta.uso)
