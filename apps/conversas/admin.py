"""Admin de conversa, somente leitura.

O cliente acompanha o atendimento aqui; editar uma conversa pela mao nao e
operacao que o produto ofereca, e deixar editavel abriria a porta para
reescrever historico de atendimento.
"""

from django.contrib import admin

from apps.common.admin import OperacaoModelAdmin, TenantScopedModelAdmin
from apps.conversas.models import Contato, Conversa, EventoWebhook, Mensagem


class SomenteLeituraMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MensagemInline(admin.TabularInline):
    model = Mensagem
    extra = 0
    can_delete = False
    fields = ("criado_em", "direcao", "status", "tipo", "texto", "categoria_cobranca")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Contato)
class ContatoAdmin(SomenteLeituraMixin, TenantScopedModelAdmin):
    list_display = ("telefone", "nome_perfil", "criado_em")
    search_fields = ("telefone", "nome_perfil")


@admin.register(Conversa)
class ConversaAdmin(SomenteLeituraMixin, TenantScopedModelAdmin):
    list_display = ("contato", "ultima_entrada_em", "janela_aberta")
    search_fields = ("contato__telefone", "contato__nome_perfil")
    inlines = [MensagemInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("contato")

    @admin.display(boolean=True, description="janela de 24h aberta")
    def janela_aberta(self, obj):
        return obj.janela_aberta()


@admin.register(EventoWebhook)
class EventoWebhookAdmin(SomenteLeituraMixin, OperacaoModelAdmin):
    """Payload cru e material de diagnostico da operacao, nao do cliente."""

    list_display = ("id_mensagem", "processado_em", "criado_em")
    search_fields = ("id_mensagem",)
