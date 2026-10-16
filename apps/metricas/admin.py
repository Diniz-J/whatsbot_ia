"""Back-office da tabela de preco: so a operacao.

E o que a plataforma paga a Meta e a Anthropic, nao o que o cliente paga pelo
plano. Deixar isso editavel pelo cliente seria entregar a planilha de margem e,
pior, deixar o custo faturado ser reescrito por quem o paga.
"""

from django.contrib import admin

from apps.common.admin import OperacaoModelAdmin
from apps.metricas.models import TabelaPreco


@admin.register(TabelaPreco)
class TabelaPrecoAdmin(OperacaoModelAdmin):
    list_display = (
        "tenant",
        "vigente_de",
        "por_mensagem_utility",
        "por_milhao_entrada",
        "por_milhao_saida",
    )
    list_filter = ("tenant",)
    autocomplete_fields = ("tenant",)
    readonly_fields = ("criado_em", "atualizado_em")
    fieldsets = (
        (None, {"fields": ("tenant", "vigente_de")}),
        (
            "Por mensagem entregue",
            {
                "fields": (
                    "por_mensagem_service",
                    "por_mensagem_utility",
                    "por_mensagem_marketing",
                    "por_mensagem_authentication",
                )
            },
        ),
        ("Por milhao de token", {"fields": ("por_milhao_entrada", "por_milhao_saida")}),
        ("Carimbo", {"fields": ("criado_em", "atualizado_em")}),
    )
