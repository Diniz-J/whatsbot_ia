"""Back-office do catalogo.

E a tela que justificou escolher Django (DESIGN.md secao 2): o dono do
negocio mexe em cardapio, preco e horario sem nos. Escopada por cliente, e
nao somente leitura — aqui ele escreve.
"""

from django.contrib import admin
from unfold.admin import TabularInline

from apps.catalogo.models import Categoria, HorarioFuncionamento, Item
from apps.common.admin import TenantScopedModelAdmin


class ItemInline(TabularInline):
    model = Item
    extra = 1
    fields = ("nome", "preco", "disponivel", "ordem")
    # O cliente nao escolhe o `tenant`: ele e herdado da categoria no save.
    exclude = ("tenant",)


@admin.register(Categoria)
class CategoriaAdmin(TenantScopedModelAdmin):
    list_display = ("nome", "ordem", "ativa", "quantos_itens")
    list_filter = ("ativa",)
    search_fields = ("nome",)
    inlines = [ItemInline]

    @admin.display(description="itens disponiveis")
    def quantos_itens(self, obj):
        return obj.itens.filter(disponivel=True).count()

    def save_formset(self, request, form, formset, change):
        """Preenche o cliente nos itens criados pelo inline.

        O campo esta fora do formulario, entao o inline salvaria sem ele e
        bateria no NOT NULL.
        """
        itens = formset.save(commit=False)
        for item in itens:
            if not item.tenant_id:
                item.tenant_id = form.instance.tenant_id
            item.save()
        for item in formset.deleted_objects:
            item.delete()
        formset.save_m2m()


@admin.register(Item)
class ItemAdmin(TenantScopedModelAdmin):
    list_display = ("nome", "categoria", "preco", "disponivel")
    list_filter = ("disponivel", "categoria")
    list_editable = ("preco", "disponivel")
    search_fields = ("nome", "descricao")
    autocomplete_fields = ("categoria",)


@admin.register(HorarioFuncionamento)
class HorarioFuncionamentoAdmin(TenantScopedModelAdmin):
    list_display = ("dia_semana", "abre", "fecha", "vira_o_dia")
    list_filter = ("dia_semana",)

    @admin.display(boolean=True, description="passa da meia-noite")
    def vira_o_dia(self, obj):
        return obj.vira_o_dia
