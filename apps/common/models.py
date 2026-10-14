"""Bases compartilhadas. Esta app nao tem modelo de dominio proprio."""

from django.db import models


class TimeStampedModel(models.Model):
    """Carimbo de criacao e atualizacao."""

    criado_em = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        abstract = True


class BaseModelTenant(TimeStampedModel):
    """Base de todo modelo de dominio: carimbo de tempo + dono.

    `PROTECT` de proposito: apagar um cliente que ainda tem conversa, pedido
    ou metrica precisa doer, porque esse historico e a base do faturamento.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
        verbose_name="cliente",
    )

    class Meta:
        abstract = True
