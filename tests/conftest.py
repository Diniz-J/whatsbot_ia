import pytest

from apps.tenants.models import Configuracao, Tenant, Usuario


@pytest.fixture
def restaurante(db):
    tenant = Tenant.objects.create(nome="Cantina do Ze", slug="cantina-do-ze")
    Configuracao.objects.create(tenant=tenant, whatsapp_phone_number_id="111")
    return tenant


@pytest.fixture
def clinica(db):
    tenant = Tenant.objects.create(nome="Clinica Sol", slug="clinica-sol")
    Configuracao.objects.create(tenant=tenant, whatsapp_phone_number_id="222")
    return tenant


@pytest.fixture
def usuario_restaurante(restaurante):
    return Usuario.objects.create_user(
        username="ze",
        password="senha-de-teste-123",
        tenant=restaurante,
        is_staff=True,
    )


@pytest.fixture
def operacao(db):
    return Usuario.objects.create_superuser(
        username="diniz",
        password="senha-de-teste-123",
    )
