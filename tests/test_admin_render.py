"""As telas do admin renderizam.

O Unfold sobrescreve template e formulario do admin (inclusive os de usuario),
e erro de fiacao aparece so quando a pagina abre — nao no `manage.py check`.
"""

import pytest


@pytest.mark.django_db
def test_login_renderiza(client):
    assert client.get("/painel/login/").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "rota",
    [
        "/painel/",
        "/painel/tenants/tenant/",
        "/painel/tenants/tenant/add/",
        "/painel/tenants/configuracao/",
        "/painel/tenants/configuracao/add/",
        "/painel/tenants/usuario/",
        "/painel/tenants/usuario/add/",
        "/painel/conversas/contato/",
        "/painel/conversas/conversa/",
        "/painel/conversas/eventowebhook/",
    ],
)
def test_telas_da_operacao_renderizam(client, operacao, rota):
    client.force_login(operacao)

    assert client.get(rota).status_code == 200


@pytest.mark.django_db
def test_detalhe_da_conversa_renderiza_com_o_inline(client, operacao, restaurante):
    """A tela com inline e a que mais quebra quando o Unfold muda de versao."""
    from apps.conversas.models import Contato, Conversa, Mensagem

    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(tenant=restaurante, contato=contato)
    Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="oi",
    )
    client.force_login(operacao)

    assert client.get(f"/painel/conversas/conversa/{conversa.pk}/change/").status_code == 200
