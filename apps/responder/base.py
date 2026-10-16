"""Contrato do atendimento: a peca que troca entre os dois tiers.

Invariante 1 (DESIGN.md secao 5): nada aqui conhece canal nem HTTP. Entra
objeto de dominio, sai objeto de dominio. E isso que faz "bot de regra" e
"hibrido com IA" serem a mesma aplicacao com um componente diferente, em vez
de dois produtos.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Pergunta:
    """O que o cliente disse, sem vocabulario de canal."""

    texto: str
    tipo: str = "text"


@dataclass(frozen=True)
class Contexto:
    """O que o atendimento precisa saber para responder.

    Carrega `tenant_id` porque toda consulta ao catalogo e escopada, e o nome
    do contato para a saudacao. Nao carrega a janela de 24h: decidir entre
    texto livre e template e politica de canal, e quem resolve e o
    agendamento, nao quem redige.
    """

    tenant_id: int
    nome_contato: str = ""


@dataclass(frozen=True)
class Resposta:
    """O que responder. `None` no lugar dela significa nao responder nada."""

    texto: str


class Responder(ABC):
    @abstractmethod
    def handle(self, pergunta: Pergunta, contexto: Contexto) -> Resposta | None:
        """Redige a resposta, ou devolve `None` para ficar calado."""
