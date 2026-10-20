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

from apps.responder.base import Autor, Turno
from apps.responder.llm import ferramentas

logger = logging.getLogger(__name__)

# Teto de voltas. Cada volta e uma chamada cobrada; modelo preso pedindo
# ferramenta queimaria token sem nunca responder.
MAX_VOLTAS = 4


class ErroLLM(Exception):
    """Falha ao obter resposta do modelo.

    Carrega o `uso` ja consumido quando a falha acontece depois de uma ou mais
    chamadas: a Anthropic cobra por esses tokens mesmo sem resposta util, e
    descartar o numero subnotificaria o custo. `None` quando nada foi gasto
    (chave ausente, ou falha antes da primeira resposta).
    """

    def __init__(self, mensagem: str, uso: "UsoDeTokens | None" = None):
        super().__init__(mensagem)
        self.uso = uso


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


def conversar(
    sistema: str,
    pergunta: str,
    tenant_id: int,
    historico: tuple[Turno, ...] = (),
    contato_id: int | None = None,
) -> RespostaLLM:
    """Pergunta ao modelo, roda as ferramentas que ele pedir, devolve o texto.

    O historico entra antes da pergunta, que fica sempre sendo o ultimo turno.
    Quem decide quanto historico vem e o pipeline; aqui ele so e traduzido.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ErroLLM("ANTHROPIC_API_KEY nao configurada")

    cliente = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=settings.LLM_TIMEOUT)
    mensagens = [*_turnos(historico), {"role": "user", "content": pergunta}]
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
            # resposta inventada sai daqui. O `uso` leva o que ja foi cobrado
            # nas voltas anteriores, quando houve.
            raise ErroLLM(f"Falha na API da Anthropic: {erro}", uso) from erro

        uso = uso.somar(UsoDeTokens(resposta.usage.input_tokens, resposta.usage.output_tokens))

        if resposta.stop_reason == "refusal":
            raise ErroLLM("Modelo recusou responder", uso)
        if resposta.stop_reason == "max_tokens":
            # Resposta cortada no limite: o texto sai pela metade e um
            # `tool_use` truncado nao da para confiar. Cair na regra e melhor
            # que mandar frase cortada ou agir sobre ferramenta incompleta.
            raise ErroLLM("Modelo truncou a resposta no limite de tokens", uso)

        pedidos = [b for b in resposta.content if b.type == "tool_use"]
        if not pedidos:
            texto = _texto_de(resposta)
            if not texto:
                raise ErroLLM("Modelo respondeu sem texto", uso)
            return RespostaLLM(texto, uso)

        mensagens.append({"role": "assistant", "content": resposta.content})
        mensagens.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": pedido.id,
                        "content": ferramentas.executar(
                            pedido.name,
                            dict(pedido.input or {}),
                            tenant_id,
                            contato_id=contato_id,
                        ),
                    }
                    for pedido in pedidos
                ],
            }
        )
        logger.debug("Volta %s: %s ferramenta(s)", volta + 1, len(pedidos))

    raise ErroLLM(f"Modelo nao concluiu em {MAX_VOLTAS} voltas de ferramenta", uso)


PAPEL = {Autor.CLIENTE: "user", Autor.ATENDENTE: "assistant"}


def _turnos(historico: tuple[Turno, ...]) -> list[dict]:
    """Historico como mensagens da API, comecando sempre em `user`.

    A API recusa conversa cuja primeira mensagem e do assistente, e a janela
    pode muito bem abrir numa fala do atendente — basta a entrada que a
    motivou ter ficado para tras do recorte. Descartar essas primeiras falas e
    melhor que um 400 que derruba o atendimento inteiro; o que se perde e o
    comeco do fio, nao a pergunta.
    """
    turnos = list(historico)
    while turnos and turnos[0].autor != Autor.CLIENTE:
        turnos.pop(0)
    return [{"role": PAPEL[turno.autor], "content": turno.texto} for turno in turnos]


def _texto_de(resposta) -> str:
    """Junta os blocos de texto. Vazio quando o modelo nao mandou texto algum.

    Quem chama decide o que fazer com o vazio, para poder levantar o erro ja
    com o `uso` gasto em maos.
    """
    partes = [b.text for b in resposta.content if b.type == "text" and b.text.strip()]
    return "\n".join(partes).strip()
