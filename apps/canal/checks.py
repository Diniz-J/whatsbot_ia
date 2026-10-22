"""Checagens de configuracao do canal, para o arranque de producao.

Existem porque as duas credenciais do webhook nascem vazias e falham fechado:
sem segredo, `assinatura_valida` recusa toda entrega, e sem token de
verificacao o handshake da Meta nunca completa. Falhar fechado e certo — o
contrario seria aceitar payload de qualquer origem — mas o sintoma e silencio:
403 em tudo, uma linha de WARNING no log e, dias depois, a Meta desativando a
subscription por acumulo de erro. Nada no caminho diz que a causa e config.

Sao checagens de deploy (`deploy=True`), entao so rodam em
`manage.py check --deploy`: em desenvolvimento e na suite as duas estao vazias
de proposito, e um erro ali pararia o trabalho de quem nao vai falar com a
Meta.

O que a Meta nao sabe e so isto. O resto do hardening de producao (redirect
HTTPS, HSTS, cookie `secure`) o proprio Django ja cobre em `security.W004`,
`W008`, `W012` e `W016`, no mesmo `--deploy` — por isso nao ha checagem nova
para `SECURE_SSL` aqui: o que faltava nao era a checagem, era rodar o
`--deploy`.
"""

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def credenciais_do_webhook(app_configs, **kwargs):
    """Recusa subir sem as credenciais que o webhook confere."""
    problemas = []

    if not settings.WHATSAPP_APP_SECRET:
        problemas.append(
            Error(
                "WHATSAPP_APP_SECRET esta vazio: nenhuma entrega da Meta sera aceita.",
                hint=(
                    "A assinatura e conferida contra este segredo e a falha e fechada, "
                    "entao toda entrega vira 403 e a Meta desativa a subscription "
                    "depois de insistir. Preencha com o app secret do app da Meta."
                ),
                id="canal.E001",
            )
        )

    if not settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        problemas.append(
            Error(
                "WHATSAPP_WEBHOOK_VERIFY_TOKEN esta vazio: o webhook nao pode ser verificado.",
                hint=(
                    "A Meta chama a URL uma vez com este token para registrar a "
                    "subscription, e token vazio sempre recusa. Preencha com o mesmo "
                    "valor configurado no painel da Meta."
                ),
                id="canal.E002",
            )
        )

    return problemas
