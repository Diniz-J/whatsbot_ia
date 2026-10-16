"""O preco que valia na epoca.

E a unica coisa desta app que nao da para derivar do historico de mensagens, e
e por isso que ela e um modelo. Preco muda e fatura fechada nao pode mudar com
ele: a Meta ja trocou a regra de cobranca uma vez (por conversa para por
mensagem, em jul/2025) e o preco do token muda a cada modelo novo. Se o
calculo lesse um preco unico de configuracao, um reajuste amanha reescreveria
o custo de todos os meses ja faturados.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.common.models import BaseModelTenant
from apps.conversas.models import CategoriaCobranca

# Preco de token e cotado por milhao em toda tabela de fornecedor.
TOKENS_POR_UNIDADE_DE_PRECO = Decimal(1_000_000)


class TabelaPreco(BaseModelTenant):
    """Uma revisao da tabela de preco, valendo a partir de `vigente_de`.

    Uma linha e a tabela inteira, nao um preco: mensagem e token sao unidades
    diferentes (por entrega e por milhao de token) e as duas chegam no mesmo
    reajuste. Separar em duas tabelas obrigaria a casar duas vigencias que na
    pratica andam juntas.

    Preco por mensagem tem seis casas de proposito: a tarifa utility no Brasil
    fica na casa do centesimo de real, e arredondar para centavo por mensagem
    erra o custo de um mes de mil entregas.
    """

    vigente_de = models.DateField(
        "vigente de",
        help_text="Primeiro dia em que esta tabela vale. A anterior continua valendo no passado.",
    )
    por_mensagem_service = models.DecimalField(
        "por mensagem service",
        max_digits=12,
        decimal_places=6,
        default=Decimal("0"),
        validators=[MinValueValidator(0)],
        help_text="Resposta na janela de 24h; hoje a Meta nao cobra no Brasil.",
    )
    por_mensagem_utility = models.DecimalField(
        "por mensagem utility",
        max_digits=12,
        decimal_places=6,
        default=Decimal("0"),
        validators=[MinValueValidator(0)],
    )
    por_mensagem_marketing = models.DecimalField(
        "por mensagem marketing",
        max_digits=12,
        decimal_places=6,
        default=Decimal("0"),
        validators=[MinValueValidator(0)],
    )
    por_mensagem_authentication = models.DecimalField(
        "por mensagem authentication",
        max_digits=12,
        decimal_places=6,
        default=Decimal("0"),
        validators=[MinValueValidator(0)],
    )
    por_milhao_entrada = models.DecimalField(
        "por milhao de token de entrada",
        max_digits=12,
        decimal_places=6,
        default=Decimal("0"),
        validators=[MinValueValidator(0)],
    )
    por_milhao_saida = models.DecimalField(
        "por milhao de token de saida",
        max_digits=12,
        decimal_places=6,
        default=Decimal("0"),
        validators=[MinValueValidator(0)],
    )

    class Meta:
        verbose_name = "tabela de preco"
        verbose_name_plural = "tabelas de preco"
        ordering = ["-vigente_de"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "vigente_de"], name="tabela_preco_unica_por_vigencia"
            )
        ]

    def __str__(self):
        return f"Tabela de {self.vigente_de:%d/%m/%Y}"

    def por_mensagem(self, categoria: str) -> Decimal:
        """Preco de uma mensagem cobravel da categoria dada.

        A resolucao por nome de campo fica aqui, com os campos, e nao em quem
        soma: assim ha um lugar so para olhar quando a Meta criar categoria
        nova, e o teste que compara as duas listas aponta para ca.
        """
        if categoria not in CategoriaCobranca.values:
            raise ValueError(f"Categoria de cobranca desconhecida: {categoria}")
        return getattr(self, f"por_mensagem_{categoria}")
