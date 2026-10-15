"""A fonte da verdade do negocio: o que vende, por quanto, e quando atende.

Existe por causa do invariante 2 (DESIGN.md secao 5): preco, horario e
disponibilidade saem daqui, nunca da cabeca do modelo. O que o dono edita no
back-office e exatamente o que o bot responde.
"""

from django.core.validators import MinValueValidator
from django.db import models

from apps.common.models import BaseModelTenant

# 0 = segunda, alinhado com `date.weekday()`.
DIAS = [
    (0, "Segunda"),
    (1, "Terca"),
    (2, "Quarta"),
    (3, "Quinta"),
    (4, "Sexta"),
    (5, "Sabado"),
    (6, "Domingo"),
]


class Categoria(BaseModelTenant):
    """Agrupamento do cardapio: entradas, pratos, bebidas."""

    nome = models.CharField("nome", max_length=80)
    ordem = models.PositiveSmallIntegerField(
        "ordem", default=0, help_text="Posicao na listagem; menor aparece primeiro."
    )
    ativa = models.BooleanField("ativa", default=True)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ["ordem", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "nome"], name="categoria_unica_por_nome")
        ]

    def __str__(self):
        return self.nome


class Item(BaseModelTenant):
    """Uma linha do cardapio."""

    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.PROTECT,
        related_name="itens",
        verbose_name="categoria",
    )
    nome = models.CharField("nome", max_length=120)
    descricao = models.TextField("descricao", blank=True)
    # Nulo de proposito: item sem preco cadastrado existe (sob consulta, peso
    # variavel). O servico precisa poder dizer "nao tenho o preco" em vez de
    # devolver zero, que o bot leria como de graca.
    preco = models.DecimalField(
        "preco",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Vazio significa sem preco cadastrado, nao gratis.",
    )
    disponivel = models.BooleanField(
        "disponivel",
        default=True,
        help_text="Desmarque quando acabar; o item continua no cardapio.",
    )
    ordem = models.PositiveSmallIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "item"
        verbose_name_plural = "itens"
        ordering = ["categoria__ordem", "ordem", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "nome"], name="item_unico_por_nome")
        ]
        indexes = [models.Index(fields=["tenant", "disponivel"])]

    def __str__(self):
        return self.nome


class HorarioFuncionamento(BaseModelTenant):
    """Uma faixa de atendimento num dia da semana.

    Uma linha por faixa, e nao uma por dia, porque almoco e jantar com
    fechamento no meio da tarde e o caso comum, nao a excecao.
    """

    dia_semana = models.PositiveSmallIntegerField("dia", choices=DIAS)
    abre = models.TimeField("abre")
    fecha = models.TimeField("fecha")

    class Meta:
        verbose_name = "horario de funcionamento"
        verbose_name_plural = "horarios de funcionamento"
        ordering = ["dia_semana", "abre"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "dia_semana", "abre"], name="horario_unico_por_inicio"
            )
        ]

    def __str__(self):
        return f"{self.get_dia_semana_display()} {self.abre:%H:%M}-{self.fecha:%H:%M}"

    @property
    def vira_o_dia(self) -> bool:
        """Faixa que atravessa a meia-noite, como 18h as 2h.

        Bar e pizzaria fecham depois da meia-noite, e tratar isso como faixa
        invalida faria o bot dizer que esta fechado no horario de maior
        movimento.
        """
        return self.fecha <= self.abre
