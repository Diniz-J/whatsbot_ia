"""Escopo das chaves estrangeiras no admin.

O `save_model` resolve o escopo do registro que se grava. Esta e a outra
metade: o escopo do que ele referencia. Sem ela, um POST forjado com o id de
uma linha do vizinho cria um registro que atravessa o escopo — foi exatamente
o furo encontrado no admin de item.
"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from apps.catalogo.admin import ItemAdmin
from apps.catalogo.models import Categoria, Item
from apps.common.admin import _escopado_por_tenant
from apps.tenants.models import Tenant, Usuario


@pytest.fixture
def admin_item():
    return ItemAdmin(Item, AdminSite())


def requisicao_de(usuario):
    requisicao = RequestFactory().get("/")
    requisicao.user = usuario
    return requisicao


def campo_categoria(admin_item, usuario):
    return admin_item.formfield_for_foreignkey(
        Item._meta.get_field("categoria"), requisicao_de(usuario)
    )


def test_cliente_so_escolhe_categoria_propria(admin_item, usuario_restaurante, clinica):
    propria = Categoria.objects.create(tenant=usuario_restaurante.tenant, nome="Pratos")
    Categoria.objects.create(tenant=clinica, nome="Consultas")

    campo = campo_categoria(admin_item, usuario_restaurante)

    assert list(campo.queryset) == [propria]


def test_operacao_escolhe_qualquer_categoria(admin_item, operacao, restaurante, clinica):
    Categoria.objects.create(tenant=restaurante, nome="Pratos")
    Categoria.objects.create(tenant=clinica, nome="Consultas")

    campo = campo_categoria(admin_item, operacao)

    assert campo.queryset.count() == 2


def test_usuario_sem_cliente_nao_escolhe_nada(admin_item, restaurante, db):
    """Falha fechada, igual ao `get_queryset`: usuario quebrado nao vira
    visao global."""
    Categoria.objects.create(tenant=restaurante, nome="Pratos")
    orfao = Usuario.objects.create_user(username="orfao", is_staff=True)

    campo = campo_categoria(admin_item, orfao)

    assert not campo.queryset.exists()


def test_modelo_com_dono_e_reconhecido():
    assert _escopado_por_tenant(Categoria) is True
    assert _escopado_por_tenant(Item) is True


def test_usuario_conta_como_escopado_sem_herdar_a_base():
    """E por isto que a checagem olha o campo e nao a heranca: `Usuario` tem
    `tenant` sem ser `BaseModelTenant`, e uma FK para ele teria o mesmo
    problema."""
    from apps.common.models import BaseModelTenant

    assert not issubclass(Usuario, BaseModelTenant)
    assert _escopado_por_tenant(Usuario) is True


def test_modelo_sem_dono_nao_e_filtrado():
    """O proprio `Tenant` nao tem dono. Filtrar por `tenant_id` nele quebraria
    em vez de proteger."""
    assert _escopado_por_tenant(Tenant) is False


def test_fk_para_modelo_sem_dono_passa_intacta(admin_item, usuario_restaurante, clinica):
    """Guarda contra filtrar demais: a FK de tenant em si nao pode ser
    estreitada por `tenant_id`, que o `Tenant` nao tem."""
    campo = admin_item.formfield_for_foreignkey(
        Item._meta.get_field("tenant"), requisicao_de(usuario_restaurante)
    )

    assert campo.queryset.count() == Tenant.objects.count()
