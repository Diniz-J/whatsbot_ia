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
    "rest_framework",
    "apps.common",
    "apps.tenants",
    "apps.conversas",
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

UNFOLD = {
    "SITE_TITLE": "whatsbot_ia",
    "SITE_HEADER": "whatsbot_ia",
    "SITE_SUBHEADER": "Atendimento no WhatsApp",
}
