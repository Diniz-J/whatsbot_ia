"""O comando que imprime o resumo do mes.

E por aqui que a operacao confere a fatura da Meta e da Anthropic, entao erro
de recorte ou de cliente tem que falhar em vez de imprimir numero errado com
cara de certo.
"""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.conversas.models import CategoriaCobranca, Contato, Conversa, Mensagem
from apps.metricas import services
from apps.metricas.models import TabelaPreco


def rodar(**opcoes) -> str:
    saida = StringIO()
    call_command("metricas_resumo", stdout=saida, **opcoes)
    return saida.getvalue()


@pytest.fixture
def movimento(restaurante):
    """Um mes com entrega cobrada, entrega gratis e token gasto."""
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(tenant=restaurante, contato=contato)
    inicio, _ = services.periodo_do_mes(2026, 1)
    dia = inicio + timedelta(days=3)

    def mensagem(direcao, **extras):
        msg = Mensagem.objects.create(
            tenant=restaurante, conversa=conversa, direcao=direcao, **extras
        )
        Mensagem.objects.filter(pk=msg.pk).update(criado_em=dia)

    mensagem(Mensagem.Direcao.ENTRADA, texto="quanto a picanha?")
    mensagem(
        Mensagem.Direcao.SAIDA,
        cobravel=False,
        categoria_cobranca=CategoriaCobranca.SERVICE,
        tokens_entrada=1_000_000,
        tokens_saida=200_000,
    )
    mensagem(Mensagem.Direcao.SAIDA, cobravel=True, categoria_cobranca=CategoriaCobranca.UTILITY)
    return restaurante


def test_imprime_volume_token_e_custo(movimento):
    TabelaPreco.objects.create(
        tenant=movimento,
        vigente_de=date(2025, 12, 1),
        por_mensagem_utility=Decimal("0.044000"),
        por_milhao_entrada=Decimal("5.500000"),
        por_milhao_saida=Decimal("27.500000"),
    )

    saida = rodar(mes="2026-01")

    assert "Cantina do Ze" in saida
    assert "01/2026" in saida
    assert "Recebidas" in saida
    assert "1.000.000" in saida
    # 1 utility a 0,044 arredonda para 0,04; token sai 11,00; total 11,04.
    assert "R$ 0,04" in saida
    assert "R$ 11,00" in saida
    assert "R$ 11,04" in saida


def test_total_fecha_com_as_linhas_exibidas(movimento):
    """As tres linhas tem que somar na tela, nao so no Decimal.

    Com 4 utility a 0,044 (0,176) e token a 1,3167, arredondar cada linha por
    conta propria e o total a parte daria 0,18 + 1,32 com total 1,49: parece
    erro de conta para quem esta conferindo a fatura.
    """
    TabelaPreco.objects.create(
        tenant=movimento,
        vigente_de=date(2025, 12, 1),
        por_mensagem_utility=Decimal("0.044000"),
        por_milhao_entrada=Decimal("5.500000"),
        por_milhao_saida=Decimal("27.500000"),
    )
    contato = Contato.objects.get(tenant=movimento)
    conversa = Conversa.objects.get(contato=contato)
    inicio, _ = services.periodo_do_mes(2026, 1)
    # Token que nao fecha em centavo redondo: 5,5 e 27,5 por milhao dao 1,3167.
    Mensagem.objects.filter(conversa=conversa, tokens_entrada__isnull=False).update(
        tokens_entrada=136_800, tokens_saida=20_520
    )
    for _ in range(3):
        extra = Mensagem.objects.create(
            tenant=movimento,
            conversa=conversa,
            direcao=Mensagem.Direcao.SAIDA,
            cobravel=True,
            categoria_cobranca=CategoriaCobranca.UTILITY,
        )
        Mensagem.objects.filter(pk=extra.pk).update(criado_em=inicio + timedelta(days=3))

    saida = rodar(mes="2026-01")

    assert "R$ 0,18" in saida
    assert "R$ 1,32" in saida
    assert "R$ 1,50" in saida


def test_quebra_a_categoria_cobrada(movimento):
    saida = rodar(mes="2026-01")

    assert "utility" in saida
    # Categoria sem entrega cobrada nao polui a saida.
    assert "marketing" not in saida


def test_sem_tabela_nao_imprime_zero(movimento):
    """R$ 0,00 seria lido como "nao custou nada"."""
    saida = rodar(mes="2026-01")

    assert "sem tabela" in saida
    assert "R$ 0,00" not in saida


def test_dispensa_o_slug_quando_ha_um_cliente_so(movimento):
    """Um banco por cliente e o caso normal (DESIGN.md secao 8)."""
    saida = rodar(mes="2026-01")

    assert "Cantina do Ze" in saida


def test_exige_o_slug_com_mais_de_um_cliente(restaurante, clinica):
    with pytest.raises(CommandError, match="--tenant"):
        rodar(mes="2026-01")


def test_escolhe_o_cliente_pelo_slug(restaurante, clinica):
    saida = rodar(tenant="clinica-sol", mes="2026-01")

    assert "Clinica Sol" in saida
    assert "Cantina do Ze" not in saida


def test_cliente_desconhecido_falha(restaurante):
    with pytest.raises(CommandError, match="nao encontrado"):
        rodar(tenant="padaria-que-nao-existe")


def test_sem_cliente_nenhum_falha(db):
    with pytest.raises(CommandError, match="Nenhum cliente"):
        rodar()


def test_mes_invalido_falha(restaurante):
    with pytest.raises(CommandError, match="YYYY-MM"):
        rodar(mes="janeiro")


def test_mes_padrao_e_o_corrente(restaurante):
    """Sem --mes o comando responde pelo mes de hoje, nao por um fixo.

    Pelo fuso local, o mesmo que o comando usa: na virada do mes as duas
    contas precisam cair no mesmo lugar.
    """
    hoje = timezone.localdate()

    saida = rodar()

    assert f"{hoje.month:02d}/{hoje.year}" in saida
