"""Rotas do projeto.

O admin NAO fica em `/admin/`: a rota vem de `ADMIN_URL` (ver DESIGN.md
secao 8). Cada instancia atende um cliente, entao o webhook aqui e o webhook dele.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("webhook/", include("apps.canal.whatsapp.urls")),
]
