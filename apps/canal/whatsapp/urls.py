"""Rota do webhook. URL unica por instancia, e cada cliente tem a sua."""

from django.urls import path

from apps.canal.whatsapp import views

urlpatterns = [
    path("whatsapp/", views.webhook, name="webhook-whatsapp"),
]
