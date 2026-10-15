"""Consultas ao catalogo. O dono e esta camada, e ela tera dois consumidores.

O `RuleResponder` chama estas funcoes, e as tools do LLM serao estas mesmas
funcoes (invariante 2). Se cada lado tivesse o seu caminho para ler preco, o
anti-alucinacao viraria teatro: bastaria um dos dois desviar.

Nada aqui depende de Django alem do ORM, para a borda poder mudar sem que o
nucleo seja reescrito.
"""

from datetime import date, datetime, time, timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.catalogo.models import Categoria, HorarioFuncionamento, Item


def cardapio(tenant_id: int) -> list[tuple[Categoria, list[Item]]]:
    """Cardapio em categorias, somente o que esta disponivel agora.

    Item indisponivel sai da lista em vez de aparecer marcado: o cliente
    perguntou o que da para pedir, nao o que existe no cadastro.
    """
    disponiveis = Prefetch(
        "itens",
        # Uma consulta para todos os itens, com o filtro de disponibilidade
        # dentro dela: sem isto seria uma consulta por categoria.
        queryset=Item.objects.filter(disponivel=True).order_by("ordem", "nome"),
        to_attr="itens_disponiveis",
    )
    categorias = (
        Categoria.objects.filter(tenant_id=tenant_id, ativa=True)
        .prefetch_related(disponiveis)
        .order_by("ordem", "nome")
    )
    return [(c, c.itens_disponiveis) for c in categorias if c.itens_disponiveis]


def buscar_itens(tenant_id: int, termo: str, incluir_indisponiveis: bool = False) -> list[Item]:
    """Itens cujo nome ou descricao casam com o termo.

    `incluir_indisponiveis` existe para o bot poder responder "temos, mas
    acabou hoje", que e diferente de "nao temos".
    """
    termo = (termo or "").strip()
    if not termo:
        return []

    consulta = Item.objects.filter(tenant_id=tenant_id).filter(
        Q(nome__icontains=termo) | Q(descricao__icontains=termo)
    )
    if not incluir_indisponiveis:
        consulta = consulta.filter(disponivel=True)
    return list(consulta.select_related("categoria").order_by("ordem", "nome"))


def horarios(tenant_id: int) -> list[HorarioFuncionamento]:
    """Todas as faixas de atendimento, para responder "que horas abre?"."""
    return list(
        HorarioFuncionamento.objects.filter(tenant_id=tenant_id).order_by("dia_semana", "abre")
    )


def esta_aberto(tenant_id: int, quando: datetime | None = None) -> bool:
    """Se o negocio esta atendendo no instante dado.

    Compara no fuso local: uma instancia atende um cliente, entao o fuso do
    projeto e o fuso dele (DESIGN.md secao 8).
    """
    agora = timezone.localtime(quando or timezone.now())
    return any(
        _cobre(faixa, agora) for faixa in HorarioFuncionamento.objects.filter(tenant_id=tenant_id)
    )


def proxima_abertura(tenant_id: int, quando: datetime | None = None) -> datetime | None:
    """Quando abre a seguir, ou `None` se nao ha horario cadastrado.

    Responde "estamos fechados, abrimos amanha as 11h" sem o modelo precisar
    inventar a data. Olha oito dias para frente para cobrir a semana inteira
    mais o dia de hoje ja comecado.
    """
    agora = timezone.localtime(quando or timezone.now())
    faixas = list(HorarioFuncionamento.objects.filter(tenant_id=tenant_id))
    if not faixas:
        return None

    candidatas = []
    for adiante in range(8):
        dia = agora.date() + timedelta(days=adiante)
        for faixa in faixas:
            if faixa.dia_semana != dia.weekday():
                continue
            abertura = _como_datetime(dia, faixa.abre, agora)
            if abertura > agora:
                candidatas.append(abertura)
    return min(candidatas) if candidatas else None


def _cobre(faixa: HorarioFuncionamento, agora: datetime) -> bool:
    """Se a faixa cobre o instante, inclusive atravessando a meia-noite."""
    hora = agora.time()
    if not faixa.vira_o_dia:
        return faixa.dia_semana == agora.weekday() and faixa.abre <= hora < faixa.fecha

    # Faixa 18h-2h vale duas coisas: depois das 18h no proprio dia, e antes
    # das 2h no dia seguinte.
    if faixa.dia_semana == agora.weekday() and hora >= faixa.abre:
        return True
    dia_anterior = (agora.weekday() - 1) % 7
    return faixa.dia_semana == dia_anterior and hora < faixa.fecha


def _como_datetime(dia: date, hora: time, referencia: datetime) -> datetime:
    return timezone.make_aware(datetime.combine(dia, hora), timezone=referencia.tzinfo)
