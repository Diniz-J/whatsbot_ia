"""Imprime o resumo de custo de um mes. E a leitura operacional da margem.

Comando e nao tela, de proposito nesta fatia: quem olha isto hoje e a operacao
da plataforma, de dentro da VPS, uma vez por mes para conferir a fatura da
Meta e da Anthropic. Tela no admin so quando o cliente precisar ver.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.conversas.models import CategoriaCobranca
from apps.metricas import services
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Imprime o resumo de custo de um mes para um cliente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            help="Slug do cliente. Dispensavel quando a instancia tem um cliente so.",
        )
        parser.add_argument("--mes", help="Mes no formato YYYY-MM. Padrao: o mes corrente.")

    def handle(self, *args, **opcoes):
        tenant = self._tenant(opcoes["tenant"])
        ano, mes = self._mes(opcoes["mes"])
        resumo = services.resumo(tenant.pk, *services.periodo_do_mes(ano, mes))

        self.stdout.write(self.style.MIGRATE_HEADING(f"{tenant.nome} — {mes:02d}/{ano}"))
        self.stdout.write("")
        self._linha("Recebidas", _inteiro(resumo.recebidas))
        self._linha("Enviadas", _inteiro(resumo.enviadas))
        self._linha("  cobradas", _inteiro(resumo.cobradas))
        for categoria in CategoriaCobranca.values:
            quantidade = resumo.cobradas_por_categoria[categoria]
            if quantidade:
                self._linha(f"    {categoria}", _inteiro(quantidade))
        if resumo.cobradas_sem_categoria:
            # Nao deveria existir; aparece em vez de sumir da soma de preco.
            self._linha("    sem categoria", _inteiro(resumo.cobradas_sem_categoria))
        self._linha("  entregues sem custo", _inteiro(resumo.entregues_sem_custo))
        self._linha("  cobranca indefinida", _inteiro(resumo.cobranca_indefinida))
        self.stdout.write("")
        self._linha("Tokens de entrada", _inteiro(resumo.tokens_entrada))
        self._linha("Tokens de saida", _inteiro(resumo.tokens_saida))
        self.stdout.write("")
        mensagens = _arredondar(resumo.custo_mensagens)
        tokens = _arredondar(resumo.custo_tokens)
        # O total soma as linhas exibidas, e nao `resumo.custo_total`: com os
        # centavos escondidos, duas linhas que sobem e um total que desce
        # parecem erro de conta para quem esta conferindo a fatura. O valor
        # exato continua no servico, para quem precisar dele.
        total = None if mensagens is None or tokens is None else mensagens + tokens
        self._linha("Custo das mensagens", _reais(mensagens))
        self._linha("Custo dos tokens", _reais(tokens))
        self._linha("Custo total", _reais(total))

    def _linha(self, rotulo: str, valor: str):
        self.stdout.write(f"{rotulo:<28}{valor:>16}")

    def _tenant(self, slug: str | None) -> Tenant:
        """Resolve o cliente, aceitando a omissao quando so existe um.

        A topologia e um banco por cliente (DESIGN.md secao 8), entao o caso
        normal e um cliente so e exigir o slug seria burocracia. Com mais de
        um, pedir e obrigatorio: imprimir o custo do vizinho por sorteio seria
        pior que falhar.
        """
        if slug:
            try:
                return Tenant.objects.get(slug=slug)
            except Tenant.DoesNotExist as erro:
                raise CommandError(f"Cliente nao encontrado: {slug}") from erro

        tenants = list(Tenant.objects.order_by("slug")[:2])
        if not tenants:
            raise CommandError("Nenhum cliente cadastrado.")
        if len(tenants) > 1:
            disponiveis = ", ".join(t.slug for t in Tenant.objects.order_by("slug"))
            raise CommandError(f"Mais de um cliente; informe --tenant. Disponiveis: {disponiveis}")
        return tenants[0]

    def _mes(self, texto: str | None) -> tuple[int, int]:
        if not texto:
            hoje = timezone.localdate()
            return hoje.year, hoje.month
        try:
            competencia = date.fromisoformat(f"{texto}-01")
        except ValueError as erro:
            raise CommandError(f"Mes invalido, use YYYY-MM: {texto}") from erro
        return competencia.year, competencia.month


def _inteiro(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _arredondar(valor: Decimal | None) -> Decimal | None:
    """Fecha o valor em centavos, preservando o indefinido."""
    if valor is None:
        return None
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _reais(valor: Decimal | None) -> str:
    """Formata em reais, ou diz que nao ha preco cadastrado.

    Nao imprime R$ 0,00 sem tabela: o zero seria lido como "nao custou nada".
    """
    if valor is None:
        return "sem tabela"
    formatado = f"{valor:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"R$ {formatado}"
