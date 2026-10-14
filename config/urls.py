"""Rotas do projeto.

O admin NAO fica em `/admin/`: a rota vem de `ADMIN_URL` (ver DESIGN.md
secao 8). O webhook da Meta e uma URL unica para todos os tenants e entra
aqui na fatia do canal.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
]
