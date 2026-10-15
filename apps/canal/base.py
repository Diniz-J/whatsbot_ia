"""Contrato do canal: o que a borda externa troca com o dominio."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MensagemEntrada:
    """Mensagem de cliente, ja normalizada e livre de formato de canal.

    E o que o `Responder` vai receber: nada aqui carrega vocabulario da Meta,
    para o nucleo nunca precisar conhecer o canal (DESIGN.md secao 5).
    """

    id_externo: str
    telefone: str
    nome_perfil: str
    tipo: str
    texto: str
    # Quando o cliente mandou, segundo a Meta. `None` quando a entrega nao
    # traz timestamp utilizavel; quem usa decide o fallback.
    enviada_em: datetime | None


@dataclass(frozen=True)
class StatusEntrega:
    """O que o canal reporta sobre uma mensagem que nos mandamos.

    `categoria` e `cobravel` vem do bloco de preco da Meta e sao a base do
    custo: desde jul/2025 a cobranca e por mensagem entregue, e a categoria
    (service, utility, marketing, authentication) e quem define o preco.
    """

    id_externo: str
    status: str
    momento: datetime | None
    erro: str
    categoria: str
    cobravel: bool | None


class ErroCanal(Exception):
    """Falha ao falar com o canal."""


class ErroTransitorio(ErroCanal):
    """Falha que vale retentar: timeout, 5xx, rate limit.

    Separada de proposito do erro permanente: retentar um numero invalido
    queima cota e nunca passa, e nao retentar um 503 perde a mensagem.
    """


class ErroPermanente(ErroCanal):
    """Falha que nao melhora com retentativa: numero invalido, template
    reprovado, token sem permissao."""


class Canal(ABC):
    """Adaptador de um canal de mensagem."""

    @abstractmethod
    def normalizar_evento(self, payload: dict) -> list[MensagemEntrada]:
        """Traduz o payload cru do canal em mensagens de dominio."""

    @abstractmethod
    def normalizar_status(self, payload: dict) -> list[StatusEntrega]:
        """Traduz o que o canal reporta sobre mensagens que ja enviamos."""

    @abstractmethod
    def enviar_texto(self, telefone: str, texto: str) -> str:
        """Envia texto livre e devolve o id que o canal atribuiu.

        So pode ser chamado com a janela de servico aberta; quem decide isso e
        o servico de envio, nao o canal.
        """

    @abstractmethod
    def enviar_template(
        self, telefone: str, template: str, idioma: str, variaveis: list[str]
    ) -> str:
        """Envia template aprovado e devolve o id que o canal atribuiu.

        E o unico caminho possivel fora da janela de 24h.
        """
