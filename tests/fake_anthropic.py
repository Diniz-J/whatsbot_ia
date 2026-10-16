"""Cliente falso da Anthropic para a suite.

Nenhum teste pode chamar a API de verdade: custa dinheiro e a resposta nao e
deterministica. Esta classe imita so a superficie que `cliente.conversar` usa.
"""

from dataclasses import dataclass, field


@dataclass
class BlocoTexto:
    text: str
    type: str = "text"


@dataclass
class BlocoFerramenta:
    name: str
    input: dict
    id: str = "toolu_1"
    type: str = "tool_use"


@dataclass
class Uso:
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class RespostaFalsa:
    content: list
    stop_reason: str = "end_turn"
    usage: Uso = field(default_factory=Uso)


class MensagensFalsas:
    def __init__(self, roteiro, erro=None):
        # Uma resposta por volta do laco, na ordem.
        self.roteiro = list(roteiro)
        self.erro = erro
        self.chamadas = []

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro is not None:
            raise self.erro
        if not self.roteiro:
            raise AssertionError("laco pediu mais voltas do que o roteiro previu")
        return self.roteiro.pop(0)


class AnthropicFalso:
    """Fabrica: `AnthropicFalso.com(roteiro)` devolve algo chamavel como a
    classe `anthropic.Anthropic`, guardando a instancia para o teste olhar."""

    ultima = None

    def __init__(self, mensagens):
        self.messages = mensagens
        AnthropicFalso.ultima = self

    @classmethod
    def com(cls, roteiro=(), erro=None):
        mensagens = MensagensFalsas(roteiro, erro)

        def construir(**kwargs):
            return cls(mensagens)

        construir.mensagens = mensagens
        return construir
