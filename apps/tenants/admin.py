"""Admin de cliente, configuracao e usuario: tudo restrito a operacao."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from apps.common.admin import OperacaoModelAdmin
from apps.tenants.models import Configuracao, Tenant, Usuario


@admin.register(Tenant)
class TenantAdmin(OperacaoModelAdmin):
    list_display = ("nome", "slug", "ativo", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "slug")
    prepopulated_fields = {"slug": ("nome",)}
    readonly_fields = ("criado_em", "atualizado_em")


@admin.register(Configuracao)
class ConfiguracaoAdmin(OperacaoModelAdmin):
    list_display = ("tenant", "responder", "whatsapp_phone_number_id")
    list_filter = ("responder",)
    search_fields = ("tenant__nome", "whatsapp_phone_number_id")
    autocomplete_fields = ("tenant",)
    readonly_fields = ("criado_em", "atualizado_em")
    fieldsets = (
        (None, {"fields": ("tenant", "responder")}),
        (
            "WhatsApp Cloud API",
            {
                "fields": (
                    "whatsapp_phone_number_id",
                    "whatsapp_business_account_id",
                    "whatsapp_access_token",
                )
            },
        ),
        ("Controle", {"fields": ("criado_em", "atualizado_em")}),
    )


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin, OperacaoModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "tenant", "is_staff", "is_superuser")
    list_filter = ("tenant", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "first_name", "last_name", "email")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Pessoal", {"fields": ("first_name", "last_name", "email")}),
        ("Cliente", {"fields": ("tenant",)}),
        (
            "Permissoes",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Datas", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "tenant", "password1", "password2"),
            },
        ),
    )
