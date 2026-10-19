"""Contrato do atendimento: a peca que troca entre os dois tiers.

Invariante 1 (DESIGN.md secao 5): nada aqui conhece canal nem HTTP. Entra
objeto de dominio, sai objeto de dominio. E isso que faz "bot de regra" e
"hibrido com IA" serem a mesma aplicacao com um componente diferente, em vez
de dois produtos.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class Pergunta:
    """O que o cliente disse, sem vocabulario de canal."""

    texto: str
    tipo: str = "text"


class Autor(StrEnum):
    """De quem foi a fala. Nao e direcao de mensagem: direcao e vocabulario de
    canal, e aqui dentro so existe quem falou."""

    CLIENTE = "cliente"
    ATENDENTE = "atendente"


@dataclass(frozen=True)
class Turno:
    """Uma fala anterior do mesmo atendimento.

    Deliberadamente pobre: autor e texto. Nada aqui sabe de status de entrega,
    id da Meta ou modelo do ORM, e e isso que deixa o atendimento receber
    historico sem passar a conhecer a tabela de mensagens (invariante 1).
    """

    autor: Autor
    texto: str


@dataclass(frozen=True)
class Contexto:
    """O que o atendimento precisa saber para responder.

    Carrega `tenant_id` porque toda consulta ao catalogo e escopada, e o nome
    do contato para a saudacao. Nao carrega a janela de 24h: decidir entre
    texto livre e template e politica de canal, e quem resolve e o
    agendamento, nao quem redige.

    `historico` sao as falas anteriores, da mais antiga para a mais nova, sem
    a pergunta atual. Tupla e nao lista porque o contexto e congelado e um
    default mutavel seria compartilhado entre instancias. Vazia quando nao ha
    o que lembrar, que e o caso do primeiro contato.
    """

    tenant_id: int
    nome_contato: str = ""
    historico: tuple[Turno, ...] = ()


@dataclass(frozen=True)
class Resposta:
    """O que responder. `None` no lugar dela significa nao responder nada.

    `uso` existe porque quem gastou token sabe quanto gastou, e e o unico
    momento em que esse numero esta disponivel. O tier de regra deixa `None`:
    nao custa token nenhum.
    """

    texto: str
    uso: object | None = None


class Responder(ABC):
    @abstractmethod
    def handle(self, pergunta: Pergunta, contexto: Contexto) -> Resposta | None:
        """Redige a resposta, ou devolve `None` para ficar calado."""
