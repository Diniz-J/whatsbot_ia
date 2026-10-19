"""O laco de ferramentas: o que ele roda, o que ele conta, quando desiste."""

from decimal import Decimal

import anthropic
import pytest

from apps.catalogo.models import Categoria, Item
from apps.responder.llm import cliente as modulo
from apps.responder.llm.cliente import ErroLLM, conversar
from tests.fake_anthropic import AnthropicFalso, BlocoFerramenta, BlocoTexto, RespostaFalsa, Uso


@pytest.fixture(autouse=True)
def chave(settings):
    settings.ANTHROPIC_API_KEY = "chave-de-teste"


@pytest.fixture
def menu(restaurante):
    categoria = Categoria.objects.create(tenant=restaurante, nome="Pratos")
    Item.objects.create(
        tenant=restaurante, categoria=categoria, nome="Picanha", preco=Decimal("89.90")
    )
    Item.objects.create(tenant=restaurante, categoria=categoria, nome="Peixe do dia", preco=None)
    return categoria


def instalar(monkeypatch, roteiro=(), erro=None):
    construir = AnthropicFalso.com(roteiro, erro)
    monkeypatch.setattr(modulo.anthropic, "Anthropic", construir)
    return construir.mensagens


def test_resposta_direta_sem_ferramenta(monkeypatch, restaurante):
    instalar(monkeypatch, [RespostaFalsa([BlocoTexto("Oi! Como posso ajudar?")])])

    resposta = conversar("sistema", "oi", restaurante.id)

    assert resposta.texto == "Oi! Como posso ajudar?"
    assert resposta.uso.entrada == 100
    assert resposta.uso.saida == 20


def test_ferramenta_e_executada_e_o_resultado_volta_ao_modelo(monkeypatch, restaurante, menu):
    mensagens = instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("buscar_item", {"termo": "picanha"})],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto("A picanha sai por R$ 89,90.")]),
        ],
    )

    resposta = conversar("sistema", "quanto custa a picanha?", restaurante.id)

    assert resposta.texto == "A picanha sai por R$ 89,90."
    # Segunda chamada leva o resultado da ferramenta, com o preco do cadastro.
    resultado = mensagens.chamadas[1]["messages"][-1]["content"][0]["content"]
    assert "Picanha" in resultado
    assert "R$ 89,90" in resultado


def test_item_sem_preco_chega_ao_modelo_como_ausencia_declarada(monkeypatch, restaurante, menu):
    """Se chegasse como campo vazio ou zero, o modelo preencheria a lacuna."""
    mensagens = instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("buscar_item", {"termo": "peixe do dia"})],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto("Vou confirmar o preco com a equipe.")]),
        ],
    )

    conversar("sistema", "quanto custa o peixe do dia?", restaurante.id)

    resultado = mensagens.chamadas[1]["messages"][-1]["content"][0]["content"]
    assert "SEM PRECO CADASTRADO" in resultado
    assert "0,00" not in resultado


def test_tokens_de_todas_as_voltas_sao_somados(monkeypatch, restaurante, menu):
    """O custo e do atendimento inteiro, nao da ultima chamada."""
    instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("ver_cardapio", {})],
                stop_reason="tool_use",
                usage=Uso(input_tokens=500, output_tokens=30),
            ),
            RespostaFalsa(
                [BlocoTexto("Temos picanha.")], usage=Uso(input_tokens=700, output_tokens=40)
            ),
        ],
    )

    resposta = conversar("sistema", "cardapio", restaurante.id)

    assert resposta.uso.entrada == 1200
    assert resposta.uso.saida == 70


def test_modelo_preso_pedindo_ferramenta_desiste(monkeypatch, restaurante, menu):
    """Cada volta e chamada cobrada; sem teto isso queima token sem responder."""
    instalar(
        monkeypatch,
        [
            RespostaFalsa([BlocoFerramenta("ver_cardapio", {})], stop_reason="tool_use")
            for _ in range(modulo.MAX_VOLTAS)
        ],
    )

    with pytest.raises(ErroLLM, match="voltas"):
        conversar("sistema", "cardapio", restaurante.id)


def test_erro_da_api_vira_erro_explicito(monkeypatch, restaurante):
    instalar(
        monkeypatch,
        erro=anthropic.APIConnectionError(request=None),
    )

    with pytest.raises(ErroLLM):
        conversar("sistema", "oi", restaurante.id)


def test_resposta_truncada_no_limite_de_tokens_vira_erro(monkeypatch, restaurante):
    """Frase cortada no meio e pior que cair na regra; stop_reason diz isso."""
    instalar(
        monkeypatch,
        [RespostaFalsa([BlocoTexto("A picanha sai por R$")], stop_reason="max_tokens")],
    )

    with pytest.raises(ErroLLM, match="truncou"):
        conversar("sistema", "quanto custa a picanha?", restaurante.id)


def test_ao_desistir_o_erro_preserva_o_token_ja_gasto(monkeypatch, restaurante, menu):
    """O token das voltas que rodaram foi cobrado mesmo sem resposta util."""
    instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("ver_cardapio", {})],
                stop_reason="tool_use",
                usage=Uso(input_tokens=300, output_tokens=25),
            )
            for _ in range(modulo.MAX_VOLTAS)
        ],
    )

    with pytest.raises(ErroLLM) as exc:
        conversar("sistema", "cardapio", restaurante.id)

    assert exc.value.uso.entrada == 300 * modulo.MAX_VOLTAS
    assert exc.value.uso.saida == 25 * modulo.MAX_VOLTAS


def test_recusa_do_modelo_vira_erro(monkeypatch, restaurante):
    instalar(monkeypatch, [RespostaFalsa([], stop_reason="refusal")])

    with pytest.raises(ErroLLM, match="recusou"):
        conversar("sistema", "oi", restaurante.id)


def test_resposta_sem_texto_vira_erro_e_nao_string_vazia(monkeypatch, restaurante):
    """Mandar mensagem vazia para o cliente final e pior que nao mandar."""
    instalar(monkeypatch, [RespostaFalsa([BlocoTexto("   ")])])

    with pytest.raises(ErroLLM, match="sem texto"):
        conversar("sistema", "oi", restaurante.id)


def test_sem_chave_configurada_falha_antes_de_chamar(monkeypatch, restaurante, settings):
    settings.ANTHROPIC_API_KEY = ""
    mensagens = instalar(monkeypatch, [RespostaFalsa([BlocoTexto("oi")])])

    with pytest.raises(ErroLLM, match="ANTHROPIC_API_KEY"):
        conversar("sistema", "oi", restaurante.id)
    assert mensagens.chamadas == []


def test_ferramenta_inventada_volta_como_dado_e_nao_derruba(monkeypatch, restaurante):
    """Modelo as vezes inventa ferramenta; devolver isso deixa ele se corrigir."""
    mensagens = instalar(
        monkeypatch,
        [
            RespostaFalsa([BlocoFerramenta("consultar_estoque", {})], stop_reason="tool_use"),
            RespostaFalsa([BlocoTexto("Vou chamar a equipe.")]),
        ],
    )

    resposta = conversar("sistema", "tem estoque?", restaurante.id)

    assert resposta.texto == "Vou chamar a equipe."
    assert "nao existe" in mensagens.chamadas[1]["messages"][-1]["content"][0]["content"]


def test_pedido_usa_modelo_e_esforco_configurados(monkeypatch, restaurante, settings):
    settings.LLM_MODEL = "claude-haiku-5-5"
    settings.LLM_EFFORT = "low"
    mensagens = instalar(monkeypatch, [RespostaFalsa([BlocoTexto("oi")])])

    conversar("sistema", "oi", restaurante.id)

    pedido = mensagens.chamadas[0]
    assert pedido["model"] == "claude-haiku-5-5"
    assert pedido["output_config"] == {"effort": "low"}
    assert pedido["thinking"] == {"type": "adaptive"}
    assert pedido["system"] == "sistema"


def test_historico_vai_antes_da_pergunta_com_os_papeis_certos(monkeypatch, restaurante):
    """A pergunta atual e sempre o ultimo turno."""
    from apps.responder.base import Autor, Turno

    mensagens = instalar(monkeypatch, [RespostaFalsa([BlocoTexto("Para quatro, as 20h.")])])

    conversar(
        "sistema",
        "quatro",
        restaurante.id,
        historico=(
            Turno(Autor.CLIENTE, "tem mesa sabado?"),
            Turno(Autor.ATENDENTE, "para quantas pessoas?"),
        ),
    )

    assert mensagens.chamadas[0]["messages"] == [
        {"role": "user", "content": "tem mesa sabado?"},
        {"role": "assistant", "content": "para quantas pessoas?"},
        {"role": "user", "content": "quatro"},
    ]


def test_historico_que_comeca_no_atendente_perde_o_inicio(monkeypatch, restaurante):
    """A API recusa conversa que comeca pelo assistente.

    A janela pode abrir numa fala do atendente — basta a entrada que a motivou
    ter ficado para tras do recorte. Perder o comeco do fio e melhor que um
    400 que derruba o atendimento inteiro.
    """
    from apps.responder.base import Autor, Turno

    mensagens = instalar(monkeypatch, [RespostaFalsa([BlocoTexto("ok")])])

    conversar(
        "sistema",
        "quero sim",
        restaurante.id,
        historico=(
            Turno(Autor.ATENDENTE, "quer reservar?"),
            Turno(Autor.ATENDENTE, "temos mesa as 20h"),
            Turno(Autor.CLIENTE, "e as 21h?"),
        ),
    )

    assert mensagens.chamadas[0]["messages"] == [
        {"role": "user", "content": "e as 21h?"},
        {"role": "user", "content": "quero sim"},
    ]


def test_historico_todo_do_atendente_sobra_so_a_pergunta(monkeypatch, restaurante):
    from apps.responder.base import Autor, Turno

    mensagens = instalar(monkeypatch, [RespostaFalsa([BlocoTexto("ok")])])

    conversar("sistema", "oi", restaurante.id, historico=(Turno(Autor.ATENDENTE, "ola"),))

    assert mensagens.chamadas[0]["messages"] == [{"role": "user", "content": "oi"}]


def test_sem_historico_a_chamada_fica_como_antes(monkeypatch, restaurante):
    """Primeiro contato nao paga token de historico nenhum."""
    mensagens = instalar(monkeypatch, [RespostaFalsa([BlocoTexto("oi")])])

    conversar("sistema", "oi", restaurante.id)

    assert mensagens.chamadas[0]["messages"] == [{"role": "user", "content": "oi"}]


def test_historico_tambem_acompanha_as_voltas_de_ferramenta(monkeypatch, restaurante):
    """O fio nao pode sumir quando o modelo pede ferramenta no meio."""
    from apps.responder.base import Autor, Turno

    mensagens = instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("ver_horarios", {})],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto("Abrimos as 19h.")]),
        ],
    )

    conversar(
        "sistema",
        "e no sabado?",
        restaurante.id,
        historico=(Turno(Autor.CLIENTE, "que horas abre?"),),
    )

    segunda = mensagens.chamadas[1]["messages"]
    assert segunda[0] == {"role": "user", "content": "que horas abre?"}
    assert segunda[1] == {"role": "user", "content": "e no sabado?"}
