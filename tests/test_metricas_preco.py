"""A tabela de preco datada, e o custo que sai dela.

Preco e a parte do custo que o historico de mensagens nao sabe: a mensagem
guarda a categoria que a Meta cobrou, nunca quanto ela custava naquele mes. Se
esta leitura pegar a tabela errada, a fatura de um mes fechado muda sozinha no
proximo reajuste.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from apps.conversas.models import CategoriaCobranca, Contato, Conversa, Mensagem
from apps.metricas import services
from apps.metricas.models import TabelaPreco


@pytest.fixture
def janeiro():
    return services.periodo_do_mes(2026, 1)


@pytest.fixture
def conversa(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    return Conversa.objects.create(tenant=restaurante, contato=contato)


def tabela(tenant, vigente_de, **precos):
    return TabelaPreco.objects.create(tenant=tenant, vigente_de=vigente_de, **precos)


def enviada(conversa, quando, **extras):
    msg = Mensagem.objects.create(
        tenant=conversa.tenant, conversa=conversa, direcao=Mensagem.Direcao.SAIDA, **extras
    )
    Mensagem.objects.filter(pk=msg.pk).update(criado_em=quando)
    return msg


def test_vigente_e_a_mais_recente_ja_comecada(restaurante):
    tabela(restaurante, date(2025, 1, 1), por_mensagem_utility=Decimal("0.030000"))
    atual = tabela(restaurante, date(2026, 1, 1), por_mensagem_utility=Decimal("0.044000"))

    assert services.tabela_vigente(restaurante.pk, date(2026, 6, 15)) == atual


def test_reajuste_futuro_nao_vale_hoje(restaurante):
    """Cadastrar o preco do mes que vem nao pode mudar o custo deste mes."""
    atual = tabela(restaurante, date(2026, 1, 1), por_mensagem_utility=Decimal("0.044000"))
    tabela(restaurante, date(2026, 7, 1), por_mensagem_utility=Decimal("0.060000"))

    assert services.tabela_vigente(restaurante.pk, date(2026, 3, 1)) == atual


def test_sem_tabela_nao_ha_vigente(restaurante):
    assert services.tabela_vigente(restaurante.pk, date(2026, 1, 1)) is None


def test_tabela_do_vizinho_nao_precifica(restaurante, clinica):
    tabela(clinica, date(2026, 1, 1), por_mensagem_utility=Decimal("9.000000"))

    assert services.tabela_vigente(restaurante.pk, date(2026, 6, 1)) is None


def test_custo_sem_tabela_e_indefinido_nao_zero(restaurante, conversa, janeiro):
    """Mes que nao custou nada e mes cujo preco ninguem cadastrou sao
    respostas diferentes, e so este campo faz a diferenca."""
    inicio, _ = janeiro
    enviada(
        conversa,
        inicio + timedelta(days=1),
        cobravel=True,
        categoria_cobranca=CategoriaCobranca.UTILITY,
    )

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.cobradas == 1
    assert resultado.custo_mensagens is None
    assert resultado.custo_tokens is None
    assert resultado.custo_total is None


def test_custo_soma_mensagem_e_token(restaurante, conversa, janeiro):
    inicio, _ = janeiro
    tabela(
        restaurante,
        date(2025, 12, 1),
        por_mensagem_utility=Decimal("0.044000"),
        por_mensagem_marketing=Decimal("0.400000"),
        por_milhao_entrada=Decimal("5.500000"),
        por_milhao_saida=Decimal("27.500000"),
    )
    dia = inicio + timedelta(days=4)
    for _ in range(3):
        enviada(conversa, dia, cobravel=True, categoria_cobranca=CategoriaCobranca.UTILITY)
    enviada(conversa, dia, cobravel=True, categoria_cobranca=CategoriaCobranca.MARKETING)
    enviada(conversa, dia, tokens_entrada=1_000_000, tokens_saida=200_000)

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.custo_mensagens == Decimal("0.532000")
    assert resultado.custo_tokens == Decimal("11.000000")
    assert resultado.custo_total == Decimal("11.532000")


def test_service_entregue_de_graca_nao_entra_no_custo(restaurante, conversa, janeiro):
    """A resposta na janela de 24h e o caso normal do produto, e e gratis.

    Ela chega com `cobravel=False`, entao nem o preco de service cadastrado
    por precaucao pode fazer o custo subir.
    """
    inicio, _ = janeiro
    tabela(restaurante, date(2025, 12, 1), por_mensagem_service=Decimal("1.000000"))
    enviada(
        conversa,
        inicio + timedelta(days=1),
        cobravel=False,
        categoria_cobranca=CategoriaCobranca.SERVICE,
    )

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.entregues_sem_custo == 1
    assert resultado.custo_mensagens == Decimal("0")


def test_cobravel_sem_categoria_nao_recebe_preco_chutado(restaurante, conversa, janeiro):
    """Sem categoria nao ha preco, e inventar um seria pior que o furo.

    A quantidade fica visivel em `cobradas_sem_categoria` para alguem conferir
    com a Meta, em vez de virar uma linha de custo sem origem.
    """
    inicio, _ = janeiro
    tabela(restaurante, date(2025, 12, 1), por_mensagem_utility=Decimal("0.044000"))
    enviada(conversa, inicio + timedelta(days=1), cobravel=True)

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.cobradas_sem_categoria == 1
    assert resultado.custo_mensagens == Decimal("0")


def test_toda_categoria_de_cobranca_tem_preco(restaurante):
    """Categoria nova na Meta tem que quebrar aqui, nao sumir da soma.

    `cobradas_por_categoria` percorre o enum; se um valor dele nao tiver campo
    de preco, o custo ignoraria essas mensagens em silencio.
    """
    tabela_atual = tabela(restaurante, date(2026, 1, 1))

    for categoria in CategoriaCobranca.values:
        assert tabela_atual.por_mensagem(categoria) == Decimal("0")


def test_categoria_desconhecida_e_erro_explicito(restaurante):
    tabela_atual = tabela(restaurante, date(2026, 1, 1))

    with pytest.raises(ValueError):
        tabela_atual.por_mensagem("conversa_paga")


def test_uma_tabela_por_data(restaurante):
    """Duas tabelas na mesma data deixariam o preco do mes no sorteio."""
    from django.db.utils import IntegrityError

    tabela(restaurante, date(2026, 1, 1))

    with pytest.raises(IntegrityError):
        tabela(restaurante, date(2026, 1, 1))


def test_cliente_nao_abre_a_tabela_de_preco(client, usuario_restaurante):
    """E a planilha de margem da plataforma, nao o plano do cliente."""
    client.force_login(usuario_restaurante)

    assert client.get("/painel/metricas/tabelapreco/").status_code == 403
