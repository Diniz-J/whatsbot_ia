"""Contrato do canal: o que a borda externa entrega para dentro.

Nesta fatia o contrato cobre so a entrada. `enviar_texto` e `enviar_template`
entram na fatia de saida, junto da implementacao que os honra — declarar agora
um metodo abstrato sem implementacao seria deixar casca morta no caminho.
"""

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


class Canal(ABC):
    """Adaptador de um canal de mensagem."""

    @abstractmethod
    def normalizar_evento(self, payload: dict) -> list[MensagemEntrada]:
        """Traduz o payload cru do canal em mensagens de dominio."""
