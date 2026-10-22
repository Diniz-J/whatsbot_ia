"""Disponibilidade de mesa: o terceiro fato que o modelo nao pode inventar.

Invariante 2 (DESIGN.md secao 5) cita preco, horario e disponibilidade. Os
dois primeiros saem de `catalogo.services`; este arquivo e o terceiro, e tem
os mesmos dois consumidores: o `RuleResponder` e as tools do LLM.

A contagem considera a reserva anotada pela equipe no back-office junto com a
que vier pelo WhatsApp — sao a mesma tabela de proposito.

Nada aqui depende de Django alem do ORM.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.catalogo import services as catalogo
from apps.conversas.models import Contato
from apps.reservas.models import DURACAO_PADRAO, STATUS_OCUPAM, Mesa, Reserva

# De quanto em quanto tempo os horarios sugeridos andam. Meia hora e o que o
# cliente espera ouvir ("19h30, 20h"); passo menor viraria lista ilegivel.
PASSO_DE_SLOT = timedelta(minutes=30)

# Teto de reservas futuras por contato. Guarda contra o bot virar a ferramenta
# mais facil de bloquear a agenda da casa: um numero de WhatsApp que marque a
# noite toda custa mesa de verdade. Quem precisa de mais fala com a equipe.
MAX_RESERVAS_ABERTAS = 3


class Motivo(StrEnum):
    """Por que o fechamento foi recusado.

    Codigo e nao frase: quem fala com o cliente escolhe a palavra, e o servico
    nao conhece canal. Cada motivo existe porque o cliente ouve coisa
    diferente em cada um — "ja passou" nao e "esta cheio".
    """

    PASSADO = "passado"
    FECHADO = "fechado"
    SEM_MESA = "sem_mesa"
    LIMITE = "limite"
    CORRIDA = "corrida"
    SEM_RESERVA = "sem_reserva"
    OUTRA_RESERVA = "outra_reserva"


class ReservaRecusada(Exception):
    """Fechamento recusado, com o motivo em `motivo`."""

    def __init__(self, motivo: Motivo):
        super().__init__(motivo.value)
        self.motivo = motivo


@dataclass(frozen=True)
class Disponibilidade:
    """Resposta de "tem mesa as 20h para 4?".

    `aberto` e separado de `mesas` porque "estamos fechados nesse horario" e
    "esta tudo reservado" sao respostas diferentes para quem perguntou, e
    juntar as duas num unico nao faria o bot dizer o motivo certo.
    """

    inicio: datetime
    fim: datetime
    pessoas: int
    aberto: bool
    mesas: list[Mesa]

    @property
    def livre(self) -> bool:
        return self.aberto and bool(self.mesas)


def disponibilidade(
    tenant_id: int,
    inicio: datetime,
    pessoas: int,
    fim: datetime | None = None,
    ignorando: int | None = None,
) -> Disponibilidade:
    """Mesas que cabem o grupo e estao livres no intervalo `[inicio, fim)`.

    Fechado nao consulta mesa: a resposta ja esta decidida, e o horario de
    funcionamento vem do mesmo `catalogo.services` que responde "que horas
    abre" — duas fontes para a mesma pergunta acabariam divergindo.

    Checa o instante de inicio, nao o intervalo inteiro: se a reserva avanca
    depois de fechar, quem decide e a casa, e nao ha politica cadastrada para
    isso.

    `ignorando` tira uma reserva da conta de ocupacao. Existe para remarcar:
    mover a reserva das 20h para 20h30 na mesma mesa esbarraria nela mesma, e
    a resposta seria "sem mesa" por causa da propria reserva que se quer
    mover.
    """
    fim = fim or inicio + DURACAO_PADRAO
    aberto = catalogo.esta_aberto(tenant_id, inicio)
    mesas = _mesas_livres(tenant_id, inicio, fim, pessoas, ignorando) if aberto else []
    return Disponibilidade(inicio=inicio, fim=fim, pessoas=pessoas, aberto=aberto, mesas=mesas)


def horarios_livres(
    tenant_id: int, dia: date, pessoas: int, agora: datetime | None = None
) -> list[datetime]:
    """Horarios do dia em que sobra mesa para o grupo.

    E o que transforma "nao tem as 20h" em "tem as 21h": sem isto o bot
    responde nao e a conversa morre, que e o oposto do que o atendimento
    inbound vende.

    Tres consultas no total, e nao uma por horario: busca as faixas, as mesas
    e as reservas do dia, e cruza em memoria. Um dia cheio tem umas poucas
    dezenas de slots, e uma consulta por slot seria consulta por mensagem de
    cliente.
    """
    agora = agora or timezone.localtime()
    todas = catalogo.horarios(tenant_id)
    faixas = [f for f in todas if f.dia_semana == dia.weekday()]
    # Faixa da vespera que atravessa a meia-noite (22h as 2h) tem a madrugada
    # dela neste dia. Sem isto `horarios_livres` nunca sugeriria 00h30 embora
    # `esta_aberto` dissesse que esta aberto — as duas respondem a mesma
    # pergunta e nao podem discordar.
    vespera = [f for f in todas if f.dia_semana == (dia.weekday() - 1) % 7 and f.vira_o_dia]
    if not faixas and not vespera:
        return []

    mesas = list(
        Mesa.objects.filter(tenant_id=tenant_id, ativa=True, capacidade__gte=pessoas).order_by(
            "capacidade", "nome"
        )
    )
    if not mesas:
        return []

    ocupacoes = _ocupacoes_do_dia(tenant_id, dia, [m.pk for m in mesas])

    livres = []
    for slot in _slots_do_dia(dia, faixas, vespera, agora.tzinfo):
        # Horario que ja passou nao e sugestao: para "tem mesa hoje?" a
        # resposta nao pode incluir o almoco.
        if slot <= agora:
            continue
        if _alguma_mesa_livre(mesas, ocupacoes, slot, slot + DURACAO_PADRAO):
            livres.append(slot)
    return sorted(livres)


def _mesas_livres(
    tenant_id: int,
    inicio: datetime,
    fim: datetime,
    pessoas: int,
    ignorando: int | None = None,
) -> list[Mesa]:
    conflitantes = Reserva.objects.filter(
        mesa=OuterRef("pk"),
        status__in=STATUS_OCUPAM,
        inicio__lt=fim,
        fim__gt=inicio,
    )
    if ignorando is not None:
        conflitantes = conflitantes.exclude(pk=ignorando)
    return list(
        Mesa.objects.filter(tenant_id=tenant_id, ativa=True, capacidade__gte=pessoas)
        .annotate(ocupada=Exists(conflitantes))
        .filter(ocupada=False)
        .order_by("capacidade", "nome")
    )


def _ocupacoes_do_dia(tenant_id: int, dia: date, mesas: list[int]) -> dict[int, list[tuple]]:
    """Intervalos ocupados por mesa, com folga de um dia para os dois lados.

    A folga existe porque reserva da vespera pode invadir a madrugada do dia
    pedido, e o slot da madrugada veria a mesa livre.
    """
    fuso = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.combine(dia - timedelta(days=1), time.min), fuso)
    fim = timezone.make_aware(datetime.combine(dia + timedelta(days=2), time.min), fuso)

    ocupacoes: dict[int, list[tuple]] = {}
    consulta = Reserva.objects.filter(
        tenant_id=tenant_id,
        mesa_id__in=mesas,
        status__in=STATUS_OCUPAM,
        inicio__lt=fim,
        fim__gt=inicio,
    ).values_list("mesa_id", "inicio", "fim")
    for mesa_id, comeco, termino in consulta:
        ocupacoes.setdefault(mesa_id, []).append((comeco, termino))
    return ocupacoes


def _slots_do_dia(dia: date, faixas, vespera, fuso):
    """Horarios de meia em meia hora dentro do que abre naquele dia.

    Faixa que atravessa a meia-noite entra duas vezes, em dias diferentes: no
    proprio dia ela vai de `abre` ate a meia-noite, e no dia seguinte vai da
    meia-noite ate `fecha`. E a mesma leitura de duas pontas que o
    `catalogo.esta_aberto` faz para a faixa 18h-2h.
    """
    meia_noite = timezone.make_aware(datetime.combine(dia, time.min), fuso)

    for faixa in vespera:
        yield from _passo(meia_noite, timezone.make_aware(datetime.combine(dia, faixa.fecha), fuso))

    for faixa in faixas:
        inicio = timezone.make_aware(datetime.combine(dia, faixa.abre), fuso)
        if faixa.vira_o_dia:
            limite = meia_noite + timedelta(days=1)
        else:
            limite = timezone.make_aware(datetime.combine(dia, faixa.fecha), fuso)
        yield from _passo(inicio, limite)


def _passo(atual: datetime, limite: datetime):
    while atual < limite:
        yield atual
        atual += PASSO_DE_SLOT


def _alguma_mesa_livre(mesas, ocupacoes, inicio: datetime, fim: datetime) -> bool:
    return any(
        all(
            not (comeco < fim and termino > inicio)
            for comeco, termino in ocupacoes.get(mesa.pk, ())
        )
        for mesa in mesas
    )


def criar_reserva(
    tenant_id: int,
    contato_id: int,
    inicio: datetime,
    pessoas: int,
    observacao: str = "",
    agora: datetime | None = None,
) -> tuple[Reserva, bool]:
    """Fecha a reserva para o contato. Devolve `(reserva, criada)`.

    `criada=False` quando o contato ja tinha reserva naquele intervalo e esta e
    devolvida em vez de uma nova. Isso nao e detalhe: o modelo pode chamar a
    ferramenta duas vezes na mesma volta, e duas reservas em mesas diferentes
    para a mesma pessoa gastariam duas mesas e confundiriam a equipe. O
    criterio e sobreposicao e nao igualdade de horario, porque quem ja tem
    20h e pede 20h30 continua querendo a mesma noite.

    A disponibilidade e reconferida aqui, nunca assumida: o modelo pode pedir
    o fechamento sem ter consultado, ou ter consultado antes de a mesa ser
    tomada. Afirmar reserva feita sem isso seria o invariante 2 violado no
    caminho que mais dói — o cliente aparece no sabado e nao tem mesa.

    Escolhe a menor mesa que serve, porque `disponibilidade` devolve ordenado
    por capacidade: queimar a mesa de seis num casal custa a mesa de seis.
    """
    agora = agora or timezone.now()
    if inicio <= agora:
        raise ReservaRecusada(Motivo.PASSADO)
    if pessoas < 1:
        raise ValueError(f"Numero de pessoas invalido: {pessoas}")

    try:
        contato = Contato.objects.get(pk=contato_id, tenant_id=tenant_id)
    except Contato.DoesNotExist as erro:
        # Guard de escopo: contato de outro cliente nunca chega aqui pelo
        # caminho normal, e se chegar e defeito, nao recusa de negocio.
        raise ValueError(f"Contato {contato_id} nao e do cliente {tenant_id}") from erro

    fim = inicio + DURACAO_PADRAO
    ja_tem = (
        Reserva.objects.filter(
            tenant_id=tenant_id,
            contato_id=contato_id,
            status__in=STATUS_OCUPAM,
            inicio__lt=fim,
            fim__gt=inicio,
        )
        .order_by("inicio")
        .first()
    )
    if ja_tem is not None:
        return ja_tem, False

    abertas = Reserva.objects.filter(
        tenant_id=tenant_id,
        contato_id=contato_id,
        status__in=STATUS_OCUPAM,
        inicio__gte=agora,
    ).count()
    if abertas >= MAX_RESERVAS_ABERTAS:
        raise ReservaRecusada(Motivo.LIMITE)

    vagas = disponibilidade(tenant_id, inicio, pessoas, fim)
    if not vagas.aberto:
        raise ReservaRecusada(Motivo.FECHADO)
    if not vagas.mesas:
        raise ReservaRecusada(Motivo.SEM_MESA)

    reserva = Reserva(
        tenant_id=tenant_id,
        mesa=vagas.mesas[0],
        contato=contato,
        nome=contato.nome_perfil or contato.telefone,
        pessoas=pessoas,
        inicio=inicio,
        fim=fim,
        observacao=observacao,
        status=Reserva.Status.CONFIRMADA,
    )
    try:
        with transaction.atomic():
            reserva.full_clean()
            reserva.save()
    except (IntegrityError, ValidationError) as erro:
        # A mesa foi tomada entre a consulta e o INSERT. A restricao do banco
        # e a validacao do modelo recusam a sobreposicao, cada uma do seu
        # lado da corrida; as duas significam a mesma coisa para o cliente.
        raise ReservaRecusada(Motivo.CORRIDA) from erro
    return reserva, True


def reservas_abertas(tenant_id: int, contato_id: int, agora: datetime | None = None):
    """Reservas futuras do contato que ainda seguram mesa, da proxima em diante."""
    agora = agora or timezone.now()
    return list(
        Reserva.objects.filter(
            tenant_id=tenant_id,
            contato_id=contato_id,
            status__in=STATUS_OCUPAM,
            inicio__gte=agora,
        )
        .select_related("mesa")
        .order_by("inicio")
    )


def cancelar_reserva(tenant_id: int, contato_id: int, agora: datetime | None = None) -> Reserva:
    """Cancela a PROXIMA reserva do contato e devolve qual foi.

    Sempre a proxima, nunca escolhida por numero ou por descricao: o cliente
    diz "quero cancelar" e o modelo nao pode decidir entre duas reservas por
    conta propria. Quem tem mais de uma e quer cancelar a outra fala com a
    equipe — errar aqui apaga mesa de quem ia aparecer.

    Cancelada e nao apagada: o historico fica para a equipe ver, e o status
    libera o horario porque `STATUS_OCUPAM` nao o inclui.
    """
    proximas = reservas_abertas(tenant_id, contato_id, agora)
    if not proximas:
        raise ReservaRecusada(Motivo.SEM_RESERVA)

    reserva = proximas[0]
    reserva.status = Reserva.Status.CANCELADA
    reserva.save(update_fields=["status", "atualizado_em"])
    return reserva


def remarcar_reserva(
    tenant_id: int,
    contato_id: int,
    novo_inicio: datetime,
    pessoas: int | None = None,
    agora: datetime | None = None,
) -> Reserva:
    """Move a PROXIMA reserva do contato para outro horario. Mesma linha.

    Move em vez de cancelar e criar de novo por dois motivos. O primeiro e
    pratico: `criar_reserva` devolve a reserva que ja existe quando o intervalo
    se sobrepoe, entao remarcar das 20h para as 21h receberia de volta a
    propria reserva das 20h em vez de uma nova — conferido antes de escrever
    isto. O segundo e de significado: remarcar e uma reserva que mudou de
    hora, nao duas reservas.

    A mesa atual e mantida quando continua livre no horario novo; mudar de
    mesa sem precisar e so confusao para a equipe.

    `pessoas` muda junto quando vem, porque o cliente que remarca costuma
    dizer que o grupo mudou de tamanho no mesmo pedido.
    """
    agora = agora or timezone.now()
    if novo_inicio <= agora:
        raise ReservaRecusada(Motivo.PASSADO)

    proximas = reservas_abertas(tenant_id, contato_id, agora)
    if not proximas:
        raise ReservaRecusada(Motivo.SEM_RESERVA)

    reserva = proximas[0]
    quantas = pessoas if pessoas is not None else reserva.pessoas
    if quantas < 1:
        raise ValueError(f"Numero de pessoas invalido: {quantas}")

    novo_fim = novo_inicio + DURACAO_PADRAO
    # Sobreposicao com outra reserva do proprio contato. A restricao do banco e
    # por mesa, entao ela nao pega: a movida iria para uma mesa e a outra
    # reserva ficaria na outra, com uma pessoa segurando duas mesas na mesma
    # hora — o gasto duplo que o `ja_tem` do `criar_reserva` impede. E
    # `disponibilidade` nao cobre isto: `ignorando` tira da conta justamente a
    # reserva que esta sendo movida, nao as outras do contato.
    #
    # Recusa em vez de escolher qual das duas vale: e a mesma razao de o
    # cancelamento so mexer na proxima, e quem tem duas resolve com a equipe.
    conflito = (
        Reserva.objects.filter(
            tenant_id=tenant_id,
            contato_id=contato_id,
            status__in=STATUS_OCUPAM,
            inicio__lt=novo_fim,
            fim__gt=novo_inicio,
        )
        .exclude(pk=reserva.pk)
        .exists()
    )
    if conflito:
        raise ReservaRecusada(Motivo.OUTRA_RESERVA)

    vagas = disponibilidade(tenant_id, novo_inicio, quantas, novo_fim, ignorando=reserva.pk)
    if not vagas.aberto:
        raise ReservaRecusada(Motivo.FECHADO)
    if not vagas.mesas:
        raise ReservaRecusada(Motivo.SEM_MESA)

    atuais = [m for m in vagas.mesas if m.pk == reserva.mesa_id]
    reserva.mesa = atuais[0] if atuais else vagas.mesas[0]
    reserva.inicio = novo_inicio
    reserva.fim = novo_fim
    reserva.pessoas = quantas
    try:
        with transaction.atomic():
            reserva.full_clean()
            reserva.save(update_fields=["mesa", "inicio", "fim", "pessoas", "atualizado_em"])
    except (IntegrityError, ValidationError) as erro:
        raise ReservaRecusada(Motivo.CORRIDA) from erro
    return reserva
