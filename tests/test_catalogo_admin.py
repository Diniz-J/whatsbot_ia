"""O back-office do catalogo e onde o dono do negocio escreve.

Diferente das telas de conversa, que sao somente leitura: e esta tela que
justificou escolher Django (DESIGN.md secao 2), entao ela tem que gravar de
verdade e nao deixar o cliente escrever fora do proprio escopo.
"""

import pytest

from apps.catalogo.models import Categoria, Item


@pytest.mark.django_db
def test_cliente_cria_categoria_pelo_admin(client, usuario_restaurante):
    usuario_restaurante.is_superuser = False
    usuario_restaurante.user_permissions.add(
        *_permissoes("add_categoria", "view_categoria", "change_categoria")
    )
    client.force_login(usuario_restaurante)

    resposta = client.post(
        "/painel/catalogo/categoria/add/",
        {
            "nome": "Pratos",
            "ordem": 1,
            "ativa": "on",
            "itens-TOTAL_FORMS": "0",
            "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
        },
    )

    assert resposta.status_code == 302
    categoria = Categoria.objects.get()
    # O campo `tenant` fica fora do formulario do cliente e e preenchido no
    # servidor: editavel, bastaria trocar o select para gravar no vizinho.
    assert categoria.tenant_id == usuario_restaurante.tenant_id


@pytest.mark.django_db
def test_item_criado_pelo_inline_herda_o_cliente(client, usuario_restaurante):
    """O `tenant` sai do formulario do inline, entao sem o save_formset ele
    bateria no NOT NULL."""
    usuario_restaurante.user_permissions.add(
        *_permissoes("add_categoria", "view_categoria", "change_categoria", "add_item")
    )
    client.force_login(usuario_restaurante)

    client.post(
        "/painel/catalogo/categoria/add/",
        {
            "nome": "Bebidas",
            "ordem": 1,
            "ativa": "on",
            "itens-TOTAL_FORMS": "1",
            "itens-INITIAL_FORMS": "0",
            "itens-MIN_NUM_FORMS": "0",
            "itens-MAX_NUM_FORMS": "1000",
            "itens-0-nome": "Suco de laranja",
            "itens-0-preco": "12.00",
            "itens-0-disponivel": "on",
            "itens-0-ordem": "1",
        },
    )

    item = Item.objects.get()
    assert item.nome == "Suco de laranja"
    assert item.tenant_id == usuario_restaurante.tenant_id


@pytest.mark.django_db
def test_cliente_nao_ve_o_catalogo_do_vizinho(client, usuario_restaurante, clinica):
    categoria_alheia = Categoria.objects.create(tenant=clinica, nome="Consultas")
    usuario_restaurante.user_permissions.add(*_permissoes("view_categoria"))
    client.force_login(usuario_restaurante)

    listagem = client.get("/painel/catalogo/categoria/")
    detalhe = client.get(f"/painel/catalogo/categoria/{categoria_alheia.pk}/change/")

    assert "Consultas" not in listagem.content.decode()
    assert detalhe.status_code == 302


def _permissoes(*codenames):
    from django.contrib.auth.models import Permission

    return list(Permission.objects.filter(codename__in=codenames))
