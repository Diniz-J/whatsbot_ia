"""App do Celery. O outbound e o processamento de inbound rodam aqui."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("whatsbot_ia")
# Namespace CELERY: so settings com esse prefixo viram config do Celery.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
