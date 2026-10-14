"""O admin nao atende na rota padrao.

`/admin/` e o primeiro caminho que scanner tenta; a rota real vem de
`ADMIN_URL` (ver DESIGN.md secao 8).
"""


def test_rota_padrao_nao_existe(client):
    assert client.get("/admin/").status_code == 404


def test_admin_atende_na_rota_configurada(client):
    resposta = client.get("/painel/")

    assert resposta.status_code == 302
    assert "/painel/login/" in resposta["Location"]
