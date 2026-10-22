"""Checagem de credencial do webhook no arranque.

As duas credenciais nascem vazias e falham fechado, o que esta certo e e
invisivel: 403 em toda entrega, um WARNING no log e a Meta desativando a
subscription dias depois. A checagem troca esse silencio por uma recusa de
subir.
"""

from django.core.checks import Error, run_checks

from apps.canal.checks import credenciais_do_webhook

PREENCHIDO = {
    "WHATSAPP_APP_SECRET": "segredo-do-app",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN": "token-combinado",
}


def preencher(settings, **sobrescritas):
    for chave, valor in {**PREENCHIDO, **sobrescritas}.items():
        setattr(settings, chave, valor)


def test_configuracao_completa_nao_reclama(settings):
    preencher(settings)

    assert credenciais_do_webhook(None) == []


def test_segredo_vazio_e_erro(settings):
    preencher(settings, WHATSAPP_APP_SECRET="")

    problemas = credenciais_do_webhook(None)

    assert [p.id for p in problemas] == ["canal.E001"]
    assert isinstance(problemas[0], Error)


def test_token_de_verificacao_vazio_e_erro(settings):
    preencher(settings, WHATSAPP_WEBHOOK_VERIFY_TOKEN="")

    problemas = credenciais_do_webhook(None)

    assert [p.id for p in problemas] == ["canal.E002"]


def test_as_duas_vazias_reclamam_as_duas(settings):
    """Uma de cada vez faria a segunda aparecer so depois de corrigir a primeira."""
    preencher(settings, WHATSAPP_APP_SECRET="", WHATSAPP_WEBHOOK_VERIFY_TOKEN="")

    assert [p.id for p in credenciais_do_webhook(None)] == ["canal.E001", "canal.E002"]


def test_a_mensagem_diz_qual_variavel_preencher(settings):
    """Quem le o erro no deploy precisa do nome da variavel, nao do sintoma."""
    preencher(settings, WHATSAPP_APP_SECRET="")

    problema = credenciais_do_webhook(None)[0]

    assert "WHATSAPP_APP_SECRET" in problema.msg
    assert problema.hint


def test_nao_roda_sem_deploy(settings):
    """Em dev e na suite as credenciais estao vazias de proposito.

    Se a checagem virar comum, `manage.py check` passa a falhar para quem nao
    vai falar com a Meta — e o CI junto.
    """
    preencher(settings, WHATSAPP_APP_SECRET="", WHATSAPP_WEBHOOK_VERIFY_TOKEN="")

    assert run_checks() == []


def test_roda_com_deploy(settings):
    """E o outro lado: registrada como deploy, `--deploy` tem que encontrar."""
    preencher(settings, WHATSAPP_APP_SECRET="", WHATSAPP_WEBHOOK_VERIFY_TOKEN="")

    encontrados = [p.id for p in run_checks(include_deployment_checks=True)]

    assert "canal.E001" in encontrados
    assert "canal.E002" in encontrados
