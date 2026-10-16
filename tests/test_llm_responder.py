"""O tier hibrido como atendimento: escopo, fallback e custo registrado."""

from decimal import Decimal

import anthropic
import pytest

from apps.catalogo.models import Categoria, Item
from apps.responder.base import Contexto, Pergunta
from apps.responder.llm import cliente as modulo_cliente
from apps.responder.llm.responder import LLMResponder
from apps.responder.registry import responder_para
from apps.responder.rules import RuleResponder
from apps.tenants.models import Configuracao
from tests.fake_anthropic import AnthropicFalso, BlocoFerramenta, BlocoTexto, RespostaFalsa


@pytest.fixture(autouse=True)
def chave(settings):
    settings.ANTHROPIC_API_KEY = "chave-de-teste"


@pytest.fixture
def menu(restaurante):
    categoria = Categoria.objects.create(tenant=restaurante, nome="Pratos")
    Item.objects.create(
        tenant=restaurante, categoria=categoria, nome="Picanha", preco=Decimal("89.90")
    )
    return categoria


def instalar(monkeypatch, roteiro=(), erro=None):
    construir = AnthropicFalso.com(roteiro, erro)
    monkeypatch.setattr(modulo_cliente.anthropic, "Anthropic", construir)
    return construir.mensagens


def test_tier_llm_resolve_para_o_responder_de_ia(restaurante):
    configuracao = restaurante.configuracao
    configuracao.responder = Configuracao.Tier.LLM
    configuracao.save(update_fields=["responder", "atualizado_em"])

    assert isinstance(responder_para(configuracao), LLMResponder)


def test_tier_rules_continua_no_de_regra(restaurante):
    assert isinstance(responder_para(restaurante.configuracao), RuleResponder)


def test_responde_em_linguagem_natural_com_dado_do_catalogo(monkeypatch, restaurante, menu):
    instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("buscar_item", {"termo": "picanha"})],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto("A picanha sai por R$ 89,90. Quer reservar?")]),
        ],
    )

    resposta = LLMResponder("Cantina do Ze").handle(
        Pergunta(texto="e a tal picanha, sai por quanto?"),
        Contexto(tenant_id=restaurante.id, nome_contato="Ze"),
    )

    assert "R$ 89,90" in resposta.texto
    assert resposta.uso.entrada > 0


def test_ferramenta_so_ve_o_catalogo_do_proprio_cliente(monkeypatch, restaurante, clinica, menu):
    """O `tenant_id` vem do contexto, nao do modelo: o atendimento da clinica
    nao alcanca o cardapio do restaurante."""
    mensagens = instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("buscar_item", {"termo": "picanha"})],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto("Nao encontrei esse item.")]),
        ],
    )

    LLMResponder("Clinica Sol").handle(
        Pergunta(texto="tem picanha?"), Contexto(tenant_id=clinica.id)
    )

    resultado = mensagens.chamadas[1]["messages"][-1]["content"][0]["content"]
    assert "Picanha" not in resultado
    assert "Nenhum item encontrado" in resultado


def test_falha_do_modelo_cai_na_regra_e_ainda_atende(monkeypatch, restaurante, menu):
    """Atender pior e melhor que nao atender: o cliente final nao tem culpa
    de a API estar fora."""
    instalar(monkeypatch, erro=anthropic.APIConnectionError(request=None))

    resposta = LLMResponder("Cantina do Ze").handle(
        Pergunta(texto="quanto custa a picanha"), Contexto(tenant_id=restaurante.id)
    )

    assert "R$ 89,90" in resposta.texto
    assert resposta.uso is None


def test_mensagem_nao_textual_vai_para_a_regra(monkeypatch, restaurante, menu):
    mensagens = instalar(monkeypatch, [RespostaFalsa([BlocoTexto("nao deveria ser usado")])])

    resposta = LLMResponder().handle(
        Pergunta(texto="", tipo="audio"), Contexto(tenant_id=restaurante.id)
    )

    assert "Nao entendi" in resposta.texto
    assert mensagens.chamadas == []
