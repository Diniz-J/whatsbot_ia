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
    ],
)
def test_telas_da_operacao_renderizam(client, operacao, rota):
    client.force_login(operacao)

    assert client.get(rota).status_code == 200
