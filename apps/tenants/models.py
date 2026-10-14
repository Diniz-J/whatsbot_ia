"""Cliente da plataforma (tenant), sua configuracao e quem loga no back-office."""

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import TimeStampedModel


class Tenant(TimeStampedModel):
    """O negocio que atende pelo WhatsApp (restaurante, escola, clinica)."""

    nome = models.CharField("nome", max_length=120)
    slug = models.SlugField("identificador", unique=True)
    ativo = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Configuracao(TimeStampedModel):
    """Tier de atendimento e credenciais de WhatsApp de um cliente.

    Fica no banco, e nao no `.env`, porque cada cliente tem o seu numero e o
    seu token, e o upsell de `rules` para `llm` precisa ser uma troca de campo
    no back-office, sem deploy.
    """

    class Tier(models.TextChoices):
        RULES = "rules", "Regras"
        LLM = "llm", "Hibrido com IA"

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.PROTECT,
        related_name="configuracao",
        verbose_name="cliente",
    )
    responder = models.CharField(
        "tier de atendimento",
        max_length=10,
        choices=Tier.choices,
        default=Tier.RULES,
    )
    # Chave que resolve o tenant no webhook (DESIGN.md secao 5, invariante 3).
    # `null` em vez de `blank`: cliente em onboarding ainda nao tem numero, e
    # o Postgres aceita varios NULL num campo unique, mas so uma string vazia.
    whatsapp_phone_number_id = models.CharField(
        "phone number id",
        max_length=40,
        unique=True,
        null=True,
        blank=True,
        help_text="Id do numero na Cloud API. Resolve o cliente no webhook.",
    )
    whatsapp_business_account_id = models.CharField(
        "waba id",
        max_length=40,
        blank=True,
    )
    # TODO: cifrar em repouso antes do primeiro cliente pagante.
    whatsapp_access_token = models.TextField("access token", blank=True)

    class Meta:
        verbose_name = "configuracao"
        verbose_name_plural = "configuracoes"
        ordering = ["tenant__nome"]

    def __str__(self):
        return f"{self.tenant.nome} ({self.get_responder_display()})"

    def save(self, *args, **kwargs):
        # Espaco em volta do id viraria um tenant que o webhook nunca acha.
        if self.whatsapp_phone_number_id:
            self.whatsapp_phone_number_id = self.whatsapp_phone_number_id.strip()
        else:
            self.whatsapp_phone_number_id = None
        super().save(*args, **kwargs)


class Usuario(AbstractUser):
    """Quem loga no back-office.

    Modelo proprio desde a primeira migration de proposito: trocar o
    `AUTH_USER_MODEL` depois exige recriar o banco.

    `tenant` nulo = operacao da plataforma, que ve todos os clientes. Qualquer
    outro usuario pertence a um cliente e so ve o que e dele.
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="usuarios",
        verbose_name="cliente",
        null=True,
        blank=True,
        help_text="Vazio apenas para a operacao da plataforma (superusuario).",
    )

    class Meta(AbstractUser.Meta):
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"

    def clean(self):
        super().clean()
        if self.tenant_id is None and not self.is_superuser:
            raise ValidationError(
                {"tenant": "Usuario sem cliente so e permitido para superusuario."}
            )
