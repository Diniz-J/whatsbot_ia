"""O resumo de custo le o historico de mensagens, e le certo.

E a base do faturamento: errar o recorte, o escopo ou a diferenca entre "a
Meta disse que nao cobra" e "a Meta ainda nao disse" cobra do cliente um
numero inventado.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.conversas.models import CategoriaCobranca, Contato, Conversa, Mensagem
from apps.metricas import services


def conversa_de(tenant, telefone="5511999999999"):
    contato = Contato.objects.create(tenant=tenant, telefone=telefone)
    return Conversa.objects.create(tenant=tenant, contato=contato)


def mensagem(conversa, direcao, quando, **extras):
    """Cria a mensagem e reposiciona `criado_em`, que e `auto_now_add`."""
    msg = Mensagem.objects.create(
        tenant=conversa.tenant, conversa=conversa, direcao=direcao, **extras
    )
    Mensagem.objects.filter(pk=msg.pk).update(criado_em=quando)
    return msg


@pytest.fixture
def janeiro():
    return services.periodo_do_mes(2026, 1)


@pytest.fixture
def conversa(restaurante):
    return conversa_de(restaurante)


def test_separa_volume_por_direcao(restaurante, conversa, janeiro):
    inicio, _ = janeiro
    dia = inicio + timedelta(days=3)
    mensagem(conversa, Mensagem.Direcao.ENTRADA, dia, texto="oi")
    mensagem(conversa, Mensagem.Direcao.ENTRADA, dia, texto="cardapio")
    mensagem(conversa, Mensagem.Direcao.SAIDA, dia, texto="segue")

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.recebidas == 2
    assert resultado.enviadas == 1


def test_cobrada_livre_e_indefinida_sao_tres_coisas(restaurante, conversa, janeiro):
    """Indefinido nao e zero: e mensagem que a Meta ainda nao precificou."""
    inicio, _ = janeiro
    dia = inicio + timedelta(days=1)
    mensagem(
        conversa,
        Mensagem.Direcao.SAIDA,
        dia,
        cobravel=True,
        categoria_cobranca=CategoriaCobranca.UTILITY,
    )
    mensagem(
        conversa,
        Mensagem.Direcao.SAIDA,
        dia,
        cobravel=False,
        categoria_cobranca=CategoriaCobranca.SERVICE,
    )
    mensagem(conversa, Mensagem.Direcao.SAIDA, dia, status=Mensagem.Status.ENFILEIRADA)

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.cobradas == 1
    assert resultado.entregues_sem_custo == 1
    assert resultado.cobranca_indefinida == 1
    assert resultado.cobradas_por_categoria[CategoriaCobranca.UTILITY] == 1
    assert resultado.cobradas_por_categoria[CategoriaCobranca.SERVICE] == 0


def test_cobravel_sem_categoria_aparece_em_vez_de_sumir(restaurante, conversa, janeiro):
    """Se a Meta cobrar sem dizer a categoria, o furo tem que ficar visivel.

    Senao a mensagem conta em `cobradas`, nao entra em nenhuma categoria e
    desaparece da soma de preco sem deixar rastro.
    """
    inicio, _ = janeiro
    mensagem(conversa, Mensagem.Direcao.SAIDA, inicio + timedelta(days=1), cobravel=True)

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.cobradas == 1
    assert sum(resultado.cobradas_por_categoria.values()) == 0
    assert resultado.cobradas_sem_categoria == 1


def test_soma_tokens_e_ignora_mensagem_sem_token(restaurante, conversa, janeiro):
    inicio, _ = janeiro
    dia = inicio + timedelta(days=2)
    mensagem(conversa, Mensagem.Direcao.SAIDA, dia, tokens_entrada=200, tokens_saida=40)
    mensagem(conversa, Mensagem.Direcao.SAIDA, dia, tokens_entrada=150, tokens_saida=30)
    mensagem(conversa, Mensagem.Direcao.SAIDA, dia, texto="resposta de regra")

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.tokens_entrada == 350
    assert resultado.tokens_saida == 70
    assert resultado.tokens_totais == 420


def test_periodo_sem_movimento_devolve_zeros(restaurante, janeiro):
    """Mes vazio e resposta valida, nao ausencia de resumo."""
    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.recebidas == 0
    assert resultado.cobradas == 0
    assert resultado.tokens_entrada == 0
    assert resultado.tokens_totais == 0


def test_recorte_e_semiaberto(restaurante, conversa, janeiro):
    """A virada do mes conta uma vez, no mes que comeca."""
    inicio, fim = janeiro
    mensagem(conversa, Mensagem.Direcao.ENTRADA, inicio, texto="primeiro instante")
    mensagem(conversa, Mensagem.Direcao.ENTRADA, fim, texto="ja e fevereiro")
    mensagem(conversa, Mensagem.Direcao.ENTRADA, inicio - timedelta(seconds=1), texto="dezembro")

    resultado = services.resumo(restaurante.pk, inicio, fim)

    assert resultado.recebidas == 1


def test_resumo_nao_ve_mensagem_do_vizinho(restaurante, clinica, janeiro):
    inicio, _ = janeiro
    dia = inicio + timedelta(days=5)
    mensagem(conversa_de(restaurante), Mensagem.Direcao.ENTRADA, dia, texto="minha")
    vizinha = conversa_de(clinica, telefone="5511888888888")
    mensagem(vizinha, Mensagem.Direcao.ENTRADA, dia, texto="do vizinho")
    mensagem(vizinha, Mensagem.Direcao.SAIDA, dia, tokens_entrada=900, tokens_saida=900)

    resultado = services.resumo(restaurante.pk, *janeiro)

    assert resultado.recebidas == 1
    assert resultado.enviadas == 0
    assert resultado.tokens_entrada == 0


def test_resumo_cabe_numa_consulta(restaurante, conversa, janeiro, django_assert_num_queries):
    """Trava o agregado condicional: e a tabela que mais cresce no banco."""
    inicio, _ = janeiro
    for _ in range(5):
        mensagem(
            conversa,
            Mensagem.Direcao.SAIDA,
            inicio + timedelta(days=1),
            cobravel=True,
            categoria_cobranca=CategoriaCobranca.UTILITY,
            tokens_entrada=10,
            tokens_saida=5,
        )

    with django_assert_num_queries(1):
        resultado = services.resumo(restaurante.pk, *janeiro)
        assert resultado.cobradas == 5


def test_toda_categoria_de_cobranca_tem_contador(restaurante, janeiro):
    """Categoria nova na Meta nao pode ficar fora do resumo em silencio."""
    resultado = services.resumo(restaurante.pk, *janeiro)

    assert set(resultado.cobradas_por_categoria) == set(CategoriaCobranca.values)


def test_periodo_do_mes_fecha_no_mes_seguinte():
    inicio, fim = services.periodo_do_mes(2026, 1)

    assert (inicio.year, inicio.month, inicio.day) == (2026, 1, 1)
    assert (fim.year, fim.month, fim.day) == (2026, 2, 1)
    assert timezone.is_aware(inicio) and timezone.is_aware(fim)


def test_periodo_do_mes_vira_o_ano_em_dezembro():
    _, fim = services.periodo_do_mes(2026, 12)

    assert (fim.year, fim.month, fim.day) == (2027, 1, 1)


def test_periodo_do_mes_recusa_mes_invalido():
    with pytest.raises(ValueError):
        services.periodo_do_mes(2026, 13)


def test_periodo_do_mes_usa_o_fuso_local():
    """O mes faturado e o do calendario do cliente, nao o de UTC.

    Em Sao Paulo (UTC-3) a virada do mes cai as 03:00 UTC: se o recorte fosse
    feito em UTC, as mensagens das primeiras horas do dia 1 cairiam na fatura
    do mes anterior.
    """
    timezone.activate(ZoneInfo("America/Sao_Paulo"))
    try:
        inicio, _ = services.periodo_do_mes(2026, 1)
    finally:
        timezone.deactivate()

    assert inicio.astimezone(UTC) == datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
