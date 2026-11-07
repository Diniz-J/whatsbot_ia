"""Configuracao do Django.

Tudo que varia por ambiente vem de variavel de ambiente (ver `.env.example`).
Credenciais de WhatsApp de cada cliente NAO ficam aqui: ficam no banco, por
tenant (ver DESIGN.md secao 8).
"""

import sys
from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

# Verdadeiro quando a suite roda. Usado para nao exigir infra de producao nem
# jogar tarefa numa fila real durante teste.
TESTING = "pytest" in sys.modules

# Sem default de proposito: subir sem chave e erro, nao conveniencia.
SECRET_KEY = config("DJANGO_SECRET_KEY")
DEBUG = config("DJANGO_DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

# Rota do admin. Nunca `admin/` em producao.
ADMIN_URL = config("ADMIN_URL", default="painel/")

INSTALLED_APPS = [
    # Unfold vem antes de django.contrib.admin: ele sobrescreve os templates.
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Pela ExclusionConstraint de reservas (sobreposicao de horario na mesa).
    "django.contrib.postgres",
    "rest_framework",
    "apps.common",
    "apps.tenants",
    "apps.canal",
    "apps.catalogo",
    "apps.conversas",
    "apps.core",
    "apps.metricas",
    "apps.reservas",
    "apps.responder",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Sem default: apontar para o banco errado por omissao e pior que nao subir.
DATABASES = {
    "default": dj_database_url.parse(
        config("DATABASE_URL"),
        conn_max_age=config("DB_CONN_MAX_AGE", default=60, cast=int),
    ),
}

AUTH_USER_MODEL = "tenants.Usuario"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Celery. Em teste a tarefa roda no processo, para a suite nao precisar de Redis.
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_TASK_ALWAYS_EAGER = TESTING
CELERY_TASK_EAGER_PROPAGATES = TESTING

# Hardening de producao. Opt-in explicito para nao quebrar dev nem a suite.
if config("SECURE_SSL", default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# WhatsApp Cloud API. Isto e do app da Meta e vale para a instancia toda; o
# numero e o token de cada cliente ficam no banco (DESIGN.md secao 8).
WHATSAPP_APP_SECRET = config("WHATSAPP_APP_SECRET", default="")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = config("WHATSAPP_WEBHOOK_VERIFY_TOKEN", default="")
WHATSAPP_API_VERSION = config("WHATSAPP_API_VERSION", default="v21.0")
WHATSAPP_TIMEOUT = config("WHATSAPP_TIMEOUT", default=10.0, cast=float)
# Tamanho maximo do corpo de texto que a Cloud API aceita. Acima disso ela
# recusa, a recusa e permanente e o cliente nao recebe nada — por isso o
# `agendar_texto` corta antes de mandar.
# CONFIRMAR na doc da Meta antes do primeiro cliente: 4096 e o numero que eu
# tinha em mente e nao conferi na fonte. O mecanismo vale para qualquer valor.
WHATSAPP_TEXTO_MAX = config("WHATSAPP_TEXTO_MAX", default=4096, cast=int)
# Em desenvolvimento, nao falar com a Meta de verdade. Em teste o default e
# ligado: desde que o bot responde sozinho, um envio sai de qualquer entrega
# processada, e uma credencial em fixture bastaria para a suite fazer chamada
# cobrada. Quem precisa afirmar sobre o envio injeta o proprio canal falso.
CANAL_EM_MEMORIA = config("CANAL_EM_MEMORIA", default=TESTING, cast=bool)

# Atendimento hibrido. O modelo default e Haiku porque o token e a margem do
# plano: um atendimento que custa mais que a mensalidade nao fecha a conta.
# Em teste a chave nasce vazia, nao importa o ambiente: um teste que esqueca
# de injetar o cliente falso cai no fallback de regra em vez de fazer uma
# chamada cobrada. Quem precisa dela injeta via `settings`.
ANTHROPIC_API_KEY = "" if TESTING else config("ANTHROPIC_API_KEY", default="")
LLM_MODEL = config("LLM_MODEL", default="claude-haiku-5-5")
LLM_EFFORT = config("LLM_EFFORT", default="low")
# Teto do que o modelo pode gerar numa resposta, nao orcamento de gasto: a
# cobranca e pelo token realmente gerado, e o prompt ja pede resposta de duas
# ou tres frases. O teto e compartilhado com o raciocinio do adaptive thinking
# e com os blocos de pedido de ferramenta, entao precisa de folga.
#
# Mas a folga tem limite do lado do canal, e nao do modelo: o texto final tem
# que caber numa mensagem de WhatsApp (`WHATSAPP_TEXTO_MAX`). 16000 projetava
# texto uma ordem de grandeza acima do que a Meta aceita — dimensionar isto
# pela cabeca do modelo e esquecer o canal foi erro meu. O corte no
# `agendar_texto` e a rede embaixo; este numero e o que evita precisar dela.
LLM_MAX_TOKENS = config("LLM_MAX_TOKENS", default=4000, cast=int)
LLM_TIMEOUT = config("LLM_TIMEOUT", default=30.0, cast=float)
# Quantas falas anteriores o atendimento recebe. Dois limites, porque cada um
# sozinho falha: o teto de turnos impede que um dia movimentado vire prompt
# gigante, e a janela de servico (no pipeline) impede que a conversa da semana
# passada volte. Historico e token de entrada em toda mensagem.
HISTORICO_MAX_TURNOS = config("HISTORICO_MAX_TURNOS", default=10, cast=int)
# Teto de token por mes de calendario. Passando dele, o atendimento cai no
# tier de regra: o cliente continua sendo atendido, pior, e a conta para de
# crescer. Zero desliga.
#
# Nasce desligado de proposito. O numero depende do volume real do cliente, e
# um default generoso cortaria o servico de quem manda mais mensagem do que eu
# supus — pior que nao ter teto. O `manage.py metricas_resumo` da o numero
# medido para preencher aqui.
LLM_TETO_TOKENS_MES = config("LLM_TETO_TOKENS_MES", default=0, cast=int)

# Quanto tempo num estado nao-final conta como travado. Generoso porque o
# timeout de envio e de segundos: o que passa disto nao esta em voo.
ENVIO_TRAVADO_MINUTOS = config("ENVIO_TRAVADO_MINUTOS", default=15, cast=int)
INBOUND_TRAVADO_MINUTOS = config("INBOUND_TRAVADO_MINUTOS", default=5, cast=int)
# Entrada gravada, mas sem atendimento, por mais que isto conta como travada. O
# atendimento leva segundos (regra) ou dezenas deles (IA, com o timeout do LLM).
RESPOSTA_TRAVADA_MINUTOS = config("RESPOSTA_TRAVADA_MINUTOS", default=5, cast=int)

# A varredura so roda se houver um processo `celery beat` no ar. Sem ele, o
# agendamento abaixo e inerte e o destravamento nao acontece — ver DEPLOY.
CELERY_BEAT_SCHEDULE = {
    "varrer-pendentes": {
        "task": "core.varrer_pendentes",
        "schedule": config("VARREDURA_SEGUNDOS", default=300, cast=int),
    },
}

UNFOLD = {
    "SITE_TITLE": "whatsbot_ia",
    "SITE_HEADER": "whatsbot_ia",
    "SITE_SUBHEADER": "Atendimento no WhatsApp",
}
