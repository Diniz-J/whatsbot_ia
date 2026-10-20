"""Escopo por cliente no admin.

E o unico guard que separa um cliente do outro no back-office, entao os dois
lados (o que ve e o que grava) tem teste.
"""

from datetime import timedelta

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.utils import timezone

from apps.common.admin import TenantScopedModelAdmin
from apps.reservas.admin import ReservaAdmin
from apps.reservas.models import Mesa, Reserva
from apps.tenants.models import Configuracao, Tenant, Usuario


@pytest.fixture
def admin_escopado():
    # Configuracao serve de veiculo: tem FK para tenant e ja existe nesta fatia.
    return TenantScopedModelAdmin(Configuracao, AdminSite())


def requisicao_de(usuario):
    requisicao = RequestFactory().get("/")
    requisicao.user = usuario
    return requisicao


def formulario_de_reserva(usuario, mesa):
    """Monta o formulario de reserva do admin como o navegador o manda.

    `Reserva` e o veiculo porque e o modelo que confere escopo no proprio
    `clean`: e a combinacao de campo escondido com validacao de escopo que
    quebrava.
    """
    admin_reserva = ReservaAdmin(Reserva, AdminSite())
    requisicao = requisicao_de(usuario)
    inicio = timezone.localtime() + timedelta(days=1)
    classe = admin_reserva.get_form(requisicao, obj=None, change=False)
    formulario = classe(
        data={
            "mesa": str(mesa.pk),
            "nome": "Joana",
            "pessoas": "2",
            "inicio_0": f"{inicio:%Y-%m-%d}",
            "inicio_1": f"{inicio:%H:%M}",
            "status": Reserva.Status.CONFIRMADA,
            "observacao": "",
        }
    )
    return admin_reserva, requisicao, formulario


def test_cliente_so_ve_o_proprio_registro(admin_escopado, usuario_restaurante, clinica):
    visiveis = admin_escopado.get_queryset(requisicao_de(usuario_restaurante))

    assert [c.tenant_id for c in visiveis] == [usuario_restaurante.tenant_id]


def test_operacao_ve_todos(admin_escopado, operacao, restaurante, clinica):
    visiveis = admin_escopado.get_queryset(requisicao_de(operacao))

    assert visiveis.count() == 2


def test_usuario_sem_cliente_nao_ve_nada(admin_escopado, restaurante, db):
    """Falha fechada: usuario quebrado nao pode virar visao global."""
    orfao = Usuario.objects.create_user(username="orfao", is_staff=True)

    assert not admin_escopado.get_queryset(requisicao_de(orfao)).exists()


def test_campo_cliente_sai_do_formulario_do_cliente(admin_escopado, usuario_restaurante, operacao):
    assert "tenant" in admin_escopado.get_exclude(requisicao_de(usuario_restaurante))
    assert "tenant" not in (admin_escopado.get_exclude(requisicao_de(operacao)) or [])


def test_gravacao_forca_o_cliente_do_usuario(admin_escopado, clinica, db):
    """Mesmo recebendo outro cliente no objeto, grava no cliente do usuario."""
    novo_tenant = Tenant.objects.create(nome="Padaria Aurora", slug="padaria-aurora")
    padeiro = Usuario.objects.create_user(username="padeiro", tenant=novo_tenant, is_staff=True)
    nova = Configuracao(tenant=clinica, whatsapp_phone_number_id="999")

    admin_escopado.save_model(requisicao_de(padeiro), nova, form=None, change=False)
    nova.refresh_from_db()

    assert nova.tenant_id == novo_tenant.id


def test_usuario_sem_cliente_nao_grava(admin_escopado, restaurante, db):
    orfao = Usuario.objects.create_user(username="orfao", is_staff=True)
    nova = Configuracao(tenant=restaurante)

    with pytest.raises(PermissionDenied):
        admin_escopado.save_model(requisicao_de(orfao), nova, form=None, change=False)


@pytest.mark.django_db
def test_cliente_nao_acessa_a_area_da_operacao(client, usuario_restaurante):
    """Tier e token de WhatsApp sao da operacao: o cliente nao chega neles."""
    client.force_login(usuario_restaurante)

    resposta = client.get("/painel/tenants/configuracao/")

    assert resposta.status_code == 403


@pytest.mark.django_db
def test_operacao_acessa_a_area_da_operacao(client, operacao):
    client.force_login(operacao)

    assert client.get("/painel/tenants/configuracao/").status_code == 200


@pytest.mark.django_db
def test_cliente_grava_registro_com_guard_de_escopo_no_clean(usuario_restaurante):
    """O campo `tenant` sai do formulario, mas a validacao precisa dele.

    O `save_model` so preenche o cliente depois de o formulario validar, e
    `Reserva.clean` recusa mesa de outro cliente. Com o `tenant` nulo na
    instancia durante a validacao, essa checagem comparava contra nulo e
    recusava toda reserva: a equipe nao conseguia anotar a que entrou por
    telefone, e disponibilidade so e verdade se a do balcao contar igual
    (DESIGN.md secao 9).
    """
    mesa = Mesa.objects.create(tenant=usuario_restaurante.tenant, nome="Mesa 1", capacidade=4)

    admin_reserva, requisicao, formulario = formulario_de_reserva(usuario_restaurante, mesa)

    assert formulario.is_valid(), formulario.errors
    admin_reserva.save_model(requisicao, formulario.save(commit=False), formulario, change=False)
    assert Reserva.objects.get().tenant_id == usuario_restaurante.tenant_id


@pytest.mark.django_db
def test_cliente_nao_grava_reserva_na_mesa_do_vizinho(usuario_restaurante, clinica):
    """A outra metade: preencher o escopo nao pode afrouxar o guard.

    A mesa do vizinho continua recusada — pelo queryset do campo e pelo
    `clean`, que agora compara contra o cliente certo em vez de contra nulo.
    """
    alheia = Mesa.objects.create(tenant=clinica, nome="Mesa do vizinho", capacidade=4)

    _, _, formulario = formulario_de_reserva(usuario_restaurante, alheia)

    assert not formulario.is_valid()
    assert "mesa" in formulario.errors


@pytest.mark.django_db
def test_operacao_continua_escolhendo_o_cliente_no_formulario(operacao, restaurante):
    """Superusuario nao tem cliente proprio: o campo segue no formulario dele."""
    mesa = Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)

    _, _, formulario = formulario_de_reserva(operacao, mesa)

    assert "tenant" in formulario.fields
    assert not formulario.is_valid()
    assert "tenant" in formulario.errors
