"""Bases de admin compartilhadas: escopo por cliente e area so da operacao."""

from django.core.exceptions import PermissionDenied
from unfold.admin import ModelAdmin


class OperacaoModelAdmin(ModelAdmin):
    """Admin visivel apenas para a operacao da plataforma (superusuario).

    Nega no codigo em vez de so deixar de conceder a permissao no grupo: tier
    de atendimento e token de WhatsApp nao podem vazar para o cliente por
    descuido de configuracao.
    """

    def has_module_permission(self, request):
        return bool(request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_superuser)


class TenantScopedModelAdmin(ModelAdmin):
    """Admin de modelo escopado por cliente.

    O usuario de um cliente so le e escreve dentro do proprio tenant. O campo
    `tenant` sai do formulario dele e e preenchido no servidor: se ficasse
    editavel, bastaria trocar o select para mover o registro para outro
    cliente.
    """

    campo_tenant = "tenant"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if not request.user.tenant_id:
            # Falha fechada: sem cliente definido, nao ha nada para ver.
            return qs.none()
        return qs.filter(**{self.campo_tenant: request.user.tenant_id})

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if not request.user.is_superuser and self.campo_tenant not in exclude:
            exclude.append(self.campo_tenant)
        return exclude

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            if not request.user.tenant_id:
                raise PermissionDenied("Usuario sem cliente nao pode gravar.")
            setattr(obj, f"{self.campo_tenant}_id", request.user.tenant_id)
        super().save_model(request, obj, form, change)
