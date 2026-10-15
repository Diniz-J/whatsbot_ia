"""Leitura do cardapio: o que o bot pode afirmar sobre o que se vende."""

from decimal import Decimal

import pytest

from apps.catalogo.models import Categoria, Item
from apps.catalogo.services import buscar_itens, cardapio


@pytest.fixture
def menu(restaurante):
    pratos = Categoria.objects.create(tenant=restaurante, nome="Pratos", ordem=1)
    bebidas = Categoria.objects.create(tenant=restaurante, nome="Bebidas", ordem=2)
    Item.objects.create(
        tenant=restaurante,
        categoria=pratos,
        nome="Picanha na brasa",
        descricao="Acompanha farofa e vinagrete",
        preco=Decimal("89.90"),
        ordem=1,
    )
    Item.objects.create(
        tenant=restaurante,
        categoria=pratos,
        nome="Moqueca de peixe",
        preco=Decimal("72.00"),
        disponivel=False,
        ordem=2,
    )
    Item.objects.create(
        tenant=restaurante, categoria=bebidas, nome="Suco de laranja", preco=Decimal("12.00")
    )
    return {"pratos": pratos, "bebidas": bebidas}


def test_cardapio_traz_so_o_que_esta_disponivel(restaurante, menu):
    """O cliente perguntou o que da para pedir, nao o que existe no cadastro."""
    resultado = cardapio(restaurante.id)

    nomes = {c.nome: [i.nome for i in itens] for c, itens in resultado}
    assert nomes == {
        "Pratos": ["Picanha na brasa"],
        "Bebidas": ["Suco de laranja"],
    }


def test_cardapio_respeita_a_ordem_das_categorias(restaurante, menu):
    assert [c.nome for c, _ in cardapio(restaurante.id)] == ["Pratos", "Bebidas"]


def test_categoria_sem_item_disponivel_sai_da_lista(restaurante, menu):
    Item.objects.filter(categoria=menu["bebidas"]).update(disponivel=False)

    assert [c.nome for c, _ in cardapio(restaurante.id)] == ["Pratos"]


def test_categoria_inativa_sai_da_lista(restaurante, menu):
    Categoria.objects.filter(pk=menu["bebidas"].pk).update(ativa=False)

    assert [c.nome for c, _ in cardapio(restaurante.id)] == ["Pratos"]


def test_cardapio_nao_faz_uma_consulta_por_categoria(restaurante, menu, django_assert_num_queries):
    """Duas consultas: categorias e itens. Sem o prefetch seria uma por
    categoria, e o cardapio e a pergunta mais comum do bot."""
    with django_assert_num_queries(2):
        cardapio(restaurante.id)


def test_cardapio_e_escopado_por_cliente(restaurante, clinica, menu):
    Categoria.objects.create(tenant=clinica, nome="Consultas")

    assert [c.nome for c, _ in cardapio(restaurante.id)] == ["Pratos", "Bebidas"]
    assert cardapio(clinica.id) == []


def test_item_sem_preco_nao_vira_zero(restaurante, menu):
    """Preco vazio significa sem preco cadastrado, nao de graca. Quem responde
    tem que poder dizer que nao sabe em vez de afirmar um numero."""
    Item.objects.create(
        tenant=restaurante, categoria=menu["pratos"], nome="Peixe do dia", preco=None
    )

    (item,) = buscar_itens(restaurante.id, "peixe do dia")
    assert item.preco is None


@pytest.mark.parametrize("termo", ["picanha", "PICANHA", "  picanha  ", "brasa"])
def test_busca_acha_por_nome_sem_depender_de_caixa(restaurante, menu, termo):
    assert [i.nome for i in buscar_itens(restaurante.id, termo)] == ["Picanha na brasa"]


def test_busca_acha_pela_descricao(restaurante, menu):
    assert [i.nome for i in buscar_itens(restaurante.id, "farofa")] == ["Picanha na brasa"]


def test_busca_esconde_indisponivel_por_padrao(restaurante, menu):
    assert buscar_itens(restaurante.id, "moqueca") == []


def test_busca_pode_incluir_indisponivel(restaurante, menu):
    """ "Temos, mas acabou hoje" e resposta diferente de "nao temos"."""
    (item,) = buscar_itens(restaurante.id, "moqueca", incluir_indisponiveis=True)

    assert item.disponivel is False


@pytest.mark.parametrize("termo", ["", "   ", None])
def test_busca_sem_termo_nao_devolve_o_cardapio_inteiro(restaurante, menu, termo):
    assert buscar_itens(restaurante.id, termo) == []


def test_busca_e_escopada_por_cliente(restaurante, clinica, menu):
    categoria = Categoria.objects.create(tenant=clinica, nome="Consultas")
    Item.objects.create(tenant=clinica, categoria=categoria, nome="Picanha da clinica")

    assert [i.nome for i in buscar_itens(restaurante.id, "picanha")] == ["Picanha na brasa"]
