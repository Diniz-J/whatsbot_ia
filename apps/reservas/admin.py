"""Back-office de mesa e reserva.

E aqui que a disponibilidade fica verdadeira: a reserva anotada pelo telefone
entra na mesma tabela que o bot consulta. Escopado por cliente, com escrita —
o dono cadastra as mesas e a equipe anota as reservas.
"""

from django.contrib import admin

from apps.common.admin import TenantScopedModelAdmin
from apps.reservas.models import Mesa, Reserva


@admin.register(Mesa)
class MesaAdmin(TenantScopedModelAdmin):
    list_display = ("nome", "capacidade", "ativa")
    list_filter = ("ativa",)
    list_editable = ("capacidade", "ativa")
    search_fields = ("nome",)


@admin.register(Reserva)
class ReservaAdmin(TenantScopedModelAdmin):
    # `mesa` fica fora do list_filter de proposito: filtro de chave
    # estrangeira lista todas as linhas relacionadas, sem passar pelo escopo
    # que o `formfield_for_foreignkey` aplica no formulario.
    list_display = ("inicio", "nome", "mesa", "pessoas", "status")
    list_filter = ("status",)
    date_hierarchy = "inicio"
    search_fields = ("nome", "observacao")
    autocomplete_fields = ("mesa", "contato")
    readonly_fields = ("criado_em", "atualizado_em")
