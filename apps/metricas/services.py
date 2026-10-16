"""Custo por mensagem e por token, por cliente (DESIGN.md secao 7).

Esta app nao mantem contador proprio de volume. O fato de cada mensagem ja
esta em `conversas.Mensagem` — direcao, categoria de cobranca que a Meta
reportou na entrega, token que a resposta gastou — e copiar isso para um
ledger paralelo criaria duas versoes da mesma verdade para divergirem no
primeiro bug de escrita. O que se le aqui e sempre derivado do historico de
mensagens, que e o registro primario.

Nada aqui depende de Django alem do ORM.
"""

from dataclasses import dataclass
from datetime import date, datetime, time

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.conversas.models import CategoriaCobranca, Mensagem


@dataclass(frozen=True)
class ResumoCusto:
    """O que um periodo movimentou para um cliente.

    Separa tres destinos possiveis de uma mensagem enviada, porque confundir
    os dois ultimos e o jeito de inventar custo: `cobradas` e o que a Meta
    disse que cobra, `entregues_sem_custo` o que ela entregou de graca (a
    resposta na janela de 24h), e `cobranca_indefinida` o que ela ainda nao
    reportou ou nunca entregou. Indefinido nao e zero.
    """

    tenant_id: int
    inicio: datetime
    fim: datetime
    recebidas: int
    enviadas: int
    cobradas: int
    cobradas_por_categoria: dict[str, int]
    entregues_sem_custo: int
    cobranca_indefinida: int
    tokens_entrada: int
    tokens_saida: int

    @property
    def cobradas_sem_categoria(self) -> int:
        """Cobraveis que vieram sem categoria, e por isso nao tem preco.

        Nao deveria acontecer — a Meta manda as duas coisas no mesmo status —
        mas se acontecer o numero aparece aqui em vez de a mensagem sumir da
        soma por categoria sem ninguem notar.
        """
        return self.cobradas - sum(self.cobradas_por_categoria.values())

    @property
    def tokens_totais(self) -> int:
        return self.tokens_entrada + self.tokens_saida


def resumo(tenant_id: int, inicio: datetime, fim: datetime) -> ResumoCusto:
    """Agrega o periodo `[inicio, fim)` numa consulta.

    Intervalo semiaberto de proposito: mes fechado com mes seguinte aberto
    nao conta a mensagem da virada duas vezes.

    Uma consulta so, com agregado condicional, porque este resumo e o que a
    tela e o faturamento vao abrir todo mes sobre a tabela que mais cresce no
    banco.
    """
    saida = Q(direcao=Mensagem.Direcao.SAIDA)
    por_categoria = {
        f"categoria_{valor}": Count("pk", filter=saida & Q(cobravel=True, categoria_cobranca=valor))
        for valor in CategoriaCobranca.values
    }

    agregados = Mensagem.objects.filter(
        tenant_id=tenant_id, criado_em__gte=inicio, criado_em__lt=fim
    ).aggregate(
        recebidas=Count("pk", filter=Q(direcao=Mensagem.Direcao.ENTRADA)),
        enviadas=Count("pk", filter=saida),
        cobradas=Count("pk", filter=saida & Q(cobravel=True)),
        entregues_sem_custo=Count("pk", filter=saida & Q(cobravel=False)),
        cobranca_indefinida=Count("pk", filter=saida & Q(cobravel__isnull=True)),
        # Coalesce porque Sum de coluna toda nula devolve None, e o resumo
        # promete inteiro: quem soma custo depois nao deveria tratar nulo.
        tokens_entrada=Coalesce(Sum("tokens_entrada"), 0),
        tokens_saida=Coalesce(Sum("tokens_saida"), 0),
        **por_categoria,
    )

    return ResumoCusto(
        tenant_id=tenant_id,
        inicio=inicio,
        fim=fim,
        recebidas=agregados["recebidas"],
        enviadas=agregados["enviadas"],
        cobradas=agregados["cobradas"],
        cobradas_por_categoria={
            valor: agregados[f"categoria_{valor}"] for valor in CategoriaCobranca.values
        },
        entregues_sem_custo=agregados["entregues_sem_custo"],
        cobranca_indefinida=agregados["cobranca_indefinida"],
        tokens_entrada=agregados["tokens_entrada"],
        tokens_saida=agregados["tokens_saida"],
    )


def periodo_do_mes(ano: int, mes: int) -> tuple[datetime, datetime]:
    """Primeiro instante do mes e primeiro do mes seguinte, no fuso local.

    No fuso local porque uma instancia atende um cliente, entao o fuso do
    projeto e o dele (DESIGN.md secao 8) e o mes que ele fatura e o do
    calendario dele, nao o de UTC.
    """
    if not 1 <= mes <= 12:
        raise ValueError(f"Mes fora da faixa: {mes}")

    primeiro = date(ano, mes, 1)
    seguinte = date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)
    fuso = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(primeiro, time.min), fuso),
        timezone.make_aware(datetime.combine(seguinte, time.min), fuso),
    )
