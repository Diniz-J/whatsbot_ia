"""Conversa com a API da Anthropic: o laco de ferramentas, escrito a mao.

Laco manual e nao o tool runner do SDK por dois motivos concretos:

- o despacho precisa passar por `FERRAMENTAS`, para o teste conferir por
  identidade que a ferramenta e a funcao de servico (invariante 2);
- o `tenant_id` tem de ser injetado aqui e nao aparecer em schema nenhum. Se
  fosse argumento que o modelo preenche, o texto do cliente final poderia
  pedir o catalogo de outro estabelecimento.

Um terceiro motivo e menor mas pesa num produto pago: o tool runner e beta.
"""

import logging
from dataclasses import dataclass

import anthropic
from django.conf import settings

from apps.responder.llm import ferramentas

logger = logging.getLogger(__name__)

# Teto de voltas. Cada volta e uma chamada cobrada; modelo preso pedindo
# ferramenta queimaria token sem nunca responder.
MAX_VOLTAS = 4


class ErroLLM(Exception):
    """Falha ao obter resposta do modelo."""


@dataclass(frozen=True)
class UsoDeTokens:
    entrada: int = 0
    saida: int = 0

    def somar(self, outro: "UsoDeTokens") -> "UsoDeTokens":
        return UsoDeTokens(self.entrada + outro.entrada, self.saida + outro.saida)


@dataclass(frozen=True)
class RespostaLLM:
    texto: str
    uso: UsoDeTokens


def conversar(sistema: str, pergunta: str, tenant_id: int) -> RespostaLLM:
    """Pergunta ao modelo, roda as ferramentas que ele pedir, devolve o texto."""
    if not settings.ANTHROPIC_API_KEY:
        raise ErroLLM("ANTHROPIC_API_KEY nao configurada")

    cliente = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=settings.LLM_TIMEOUT)
    mensagens = [{"role": "user", "content": pergunta}]
    uso = UsoDeTokens()

    for volta in range(MAX_VOLTAS):
        try:
            resposta = cliente.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=settings.LLM_MAX_TOKENS,
                system=sistema,
                tools=ferramentas.SCHEMAS,
                # Pensamento adaptativo com esforco baixo: a pergunta e
                # simples e o token e a margem do plano. Desligar o
                # pensamento deixa o modelo escrever chamada de ferramenta
                # dentro do texto visivel em vez de pedi-la de verdade.
                thinking={"type": "adaptive"},
                output_config={"effort": settings.LLM_EFFORT},
                messages=mensagens,
            )
        except anthropic.APIError as erro:
            # Erro explicito para quem chamou decidir o fallback; nenhuma
            # resposta inventada sai daqui.
            raise ErroLLM(f"Falha na API da Anthropic: {erro}") from erro

        uso = uso.somar(UsoDeTokens(resposta.usage.input_tokens, resposta.usage.output_tokens))

        if resposta.stop_reason == "refusal":
            raise ErroLLM("Modelo recusou responder")

        pedidos = [b for b in resposta.content if b.type == "tool_use"]
        if not pedidos:
            return RespostaLLM(_texto_de(resposta), uso)

        mensagens.append({"role": "assistant", "content": resposta.content})
        mensagens.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": pedido.id,
                        "content": ferramentas.executar(
                            pedido.name, dict(pedido.input or {}), tenant_id
                        ),
                    }
                    for pedido in pedidos
                ],
            }
        )
        logger.debug("Volta %s: %s ferramenta(s)", volta + 1, len(pedidos))

    raise ErroLLM(f"Modelo nao concluiu em {MAX_VOLTAS} voltas de ferramenta")


def _texto_de(resposta) -> str:
    """Junta os blocos de texto. Resposta sem texto e erro, nao string vazia."""
    partes = [b.text for b in resposta.content if b.type == "text" and b.text.strip()]
    if not partes:
        raise ErroLLM("Modelo respondeu sem texto")
    return "\n".join(partes).strip()
