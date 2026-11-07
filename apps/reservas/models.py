"""Mesa e reserva: o que ocupa lugar e quando.

Existe pelo mesmo motivo do catalogo (invariante 2, DESIGN.md secao 5):
disponibilidade e fato, nao opiniao do modelo. A reserva que a equipe anota no
back-office e a que o bot consulta — se fossem tabelas diferentes, a resposta
"tem mesa as 20h" seria verdadeira so por acaso.
"""

from datetime import timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.common.models import BaseModelTenant

# Quanto tempo uma reserva ocupa a mesa quando ninguem informa o fim. Duas
# horas e a praxe de restaurante; o campo `fim` fica gravado, entao mudar o
# padrao amanha nao reescreve o que ja foi anotado.
DURACAO_PADRAO = timedelta(hours=2)

# Codigo da recusa por horario ja ocupado. O servico de fechamento decide por
# ele, e nao pela frase: so esta recusa significa "a mesa foi tomada".
SOBREPOSICAO = "sobreposicao"


class TsTzRange(models.Func):
    """`tstzrange(inicio, fim, '[)')` para a restricao de sobreposicao."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Mesa(BaseModelTenant):
    """Lugar que pode ser reservado.

    `ativa` separado de existir: mesa em reforma sai da disponibilidade sem
    apagar as reservas antigas dela, que o `PROTECT` da reserva nem deixaria.
    """

    nome = models.CharField("nome", max_length=60)
    capacidade = models.PositiveSmallIntegerField(
        "capacidade", validators=[MinValueValidator(1)], help_text="Quantas pessoas sentam."
    )
    ativa = models.BooleanField(
        "ativa", default=True, help_text="Desmarque para tirar da disponibilidade sem excluir."
    )

    class Meta:
        verbose_name = "mesa"
        verbose_name_plural = "mesas"
        ordering = ["capacidade", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "nome"], name="mesa_unica_por_nome")
        ]

    def __str__(self):
        return f"{self.nome} ({self.capacidade})"


class Reserva(BaseModelTenant):
    """Uma mesa ocupada de `inicio` a `fim`.

    `contato` e opcional porque reserva tambem entra por telefone e no balcao,
    e disponibilidade so e verdade se a anotada pela equipe contar igual.
    """

    class Status(models.TextChoices):
        CONFIRMADA = "confirmada", "Confirmada"
        CANCELADA = "cancelada", "Cancelada"
        COMPARECEU = "compareceu", "Compareceu"
        NAO_COMPARECEU = "nao_compareceu", "Nao compareceu"

    mesa = models.ForeignKey(
        Mesa, on_delete=models.PROTECT, related_name="reservas", verbose_name="mesa"
    )
    contato = models.ForeignKey(
        "conversas.Contato",
        on_delete=models.PROTECT,
        related_name="reservas",
        null=True,
        blank=True,
        verbose_name="contato",
        help_text="Vazio quando a reserva nao veio pelo WhatsApp.",
    )
    nome = models.CharField("nome de quem reservou", max_length=120)
    pessoas = models.PositiveSmallIntegerField("pessoas", validators=[MinValueValidator(1)])
    inicio = models.DateTimeField("inicio", db_index=True)
    fim = models.DateTimeField(
        "fim", blank=True, help_text="Em branco usa a duracao padrao de duas horas."
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.CONFIRMADA
    )
    observacao = models.TextField("observacao", blank=True)

    class Meta:
        verbose_name = "reserva"
        verbose_name_plural = "reservas"
        ordering = ["-inicio"]
        constraints = [
            # No banco, e nao so no servico: duas reservas da mesma mesa em
            # horarios que se cruzam sao duas pessoas sentando no mesmo lugar,
            # e dois workers gravando ao mesmo tempo passariam por qualquer
            # checagem feita em Python. Exige a extensao btree_gist para o `=`
            # da mesa conviver com o `&&` do intervalo.
            ExclusionConstraint(
                name="reserva_sem_sobreposicao_na_mesa",
                expressions=[
                    (TsTzRange("inicio", "fim", RangeBoundary()), RangeOperators.OVERLAPS),
                    ("mesa", RangeOperators.EQUAL),
                ],
                condition=models.Q(status__in=["confirmada", "compareceu"]),
                violation_error_message="Esta mesa ja tem reserva nesse horario.",
            )
        ]

    def __str__(self):
        return f"{self.nome} | {self.mesa.nome} | {self.inicio:%d/%m %H:%M}"

    def save(self, *args, **kwargs):
        # Tambem aqui e nao so no `clean`: servico e shell gravam sem passar
        # por validacao de formulario, e `fim` nulo bate no NOT NULL.
        if self.inicio and not self.fim:
            self.fim = self.inicio + DURACAO_PADRAO
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.inicio and not self.fim:
            self.fim = self.inicio + DURACAO_PADRAO
        if self.inicio and self.fim and self.fim <= self.inicio:
            raise ValidationError({"fim": "O fim tem que ser depois do inicio."})

        if self.mesa_id and self.mesa.tenant_id != self.tenant_id:
            raise ValidationError({"mesa": "A mesa pertence a outro cliente."})
        if self.contato_id and self.contato.tenant_id != self.tenant_id:
            raise ValidationError({"contato": "O contato pertence a outro cliente."})

        if self.mesa_id and self.pessoas and self.pessoas > self.mesa.capacidade:
            raise ValidationError(
                {"pessoas": f"{self.mesa.nome} acomoda {self.mesa.capacidade} pessoas."}
            )

        # A restricao do banco ja recusa a sobreposicao, mas ela chega como
        # IntegrityError no meio do request. Checar aqui devolve o erro no
        # campo, no formulario do back-office.
        if self.mesa_id and self.inicio and self.fim and self.status in STATUS_OCUPAM:
            conflito = Reserva.objects.filter(
                mesa_id=self.mesa_id,
                status__in=STATUS_OCUPAM,
                inicio__lt=self.fim,
                fim__gt=self.inicio,
            ).exclude(pk=self.pk)
            if conflito.exists():
                # Com codigo: quem fecha reserva precisa distinguir esta
                # recusa (a mesa foi tomada) de qualquer outra regra deste
                # `clean`, para nao dizer ao cliente que houve corrida quando
                # o defeito foi outro.
                raise ValidationError(
                    {
                        "inicio": ValidationError(
                            "Esta mesa ja tem reserva nesse horario.", code=SOBREPOSICAO
                        )
                    }
                )


# Status que seguram a mesa. Cancelada e nao-compareceu liberam o horario, e e
# por isso que a disponibilidade filtra por este conjunto em vez de contar
# toda linha da tabela.
STATUS_OCUPAM = (Reserva.Status.CONFIRMADA, Reserva.Status.COMPARECEU)
