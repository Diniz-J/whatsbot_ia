"""Invariantes do cliente, da configuracao e do usuario."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.tenants.models import Configuracao, Tenant, Usuario


def test_usuario_sem_cliente_so_e_valido_para_superusuario(db):
    comum = Usuario(username="sem-cliente", is_staff=True)
    with pytest.raises(ValidationError) as erro:
        comum.full_clean()
    assert "tenant" in erro.value.message_dict

    superusuario = Usuario(username="operacao", is_superuser=True, is_staff=True)
    superusuario.set_password("senha-de-teste-123")
    superusuario.full_clean()


def test_phone_number_id_e_unico(restaurante, db):
    outro = Tenant.objects.create(nome="Outro", slug="outro")
    with pytest.raises(IntegrityError):
        Configuracao.objects.create(tenant=outro, whatsapp_phone_number_id="111")


def test_dois_clientes_podem_ficar_sem_phone_number_id(db):
    """Cliente em onboarding ainda nao tem numero, e o unique nao pode travar
    o segundo cadastro. Por isso o campo e `null` e nao `blank`."""
    primeiro = Tenant.objects.create(nome="Primeiro", slug="primeiro")
    segundo = Tenant.objects.create(nome="Segundo", slug="segundo")
    Configuracao.objects.create(tenant=primeiro)
    Configuracao.objects.create(tenant=segundo)

    assert Configuracao.objects.filter(whatsapp_phone_number_id__isnull=True).count() == 2


def test_phone_number_id_e_salvo_sem_espaco(restaurante):
    config = restaurante.configuracao
    config.whatsapp_phone_number_id = "  555  "
    config.save()
    config.refresh_from_db()

    assert config.whatsapp_phone_number_id == "555"


def test_tier_padrao_e_regras(restaurante):
    assert restaurante.configuracao.responder == Configuracao.Tier.RULES
