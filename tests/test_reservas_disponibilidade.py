"""Disponibilidade de mesa.

E o terceiro fato do invariante 2, ao lado de preco e horario: o bot pode
responder "tem mesa as 20h" somente se isto responder, e isto conta tambem a
reserva que a equipe anotou no balcao.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.catalogo.models import HorarioFuncionamento
from apps.reservas import services
from apps.reservas.models import Mesa, Reserva

FUSO = ZoneInfo("America/Sao_Paulo")
# Sabado, para a faixa de funcionamento cair sempre no mesmo dia da semana.
SABADO = date(2026, 3, 7)


def as_horas(dia: date, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), tzinfo=FUSO)


@pytest.fixture(autouse=True)
def fuso_do_negocio(settings):
    """Uma instancia atende um cliente, entao o fuso do projeto e o dele."""
    settings.TIME_ZONE = "America/Sao_Paulo"
    timezone.activate(FUSO)
    yield
    timezone.deactivate()


@pytest.fixture
def aberto_no_sabado(restaurante):
    return HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SABADO.weekday(), abre=time(19, 0), fecha=time(23, 0)
    )


@pytest.fixture
def mesa_de_quatro(restaurante):
    return Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)


def reservar(tenant, mesa, inicio, **extras):
    return Reserva.objects.create(
        tenant=tenant,
        mesa=mesa,
        nome=extras.pop("nome", "Ana"),
        pessoas=extras.pop("pessoas", 2),
        inicio=inicio,
        **extras,
    )


def test_mesa_livre_no_horario_aberto(restaurante, aberto_no_sabado, mesa_de_quatro):
    resultado = services.disponibilidade(restaurante.pk, as_horas(SABADO, 20), pessoas=4)

    assert resultado.aberto
    assert resultado.livre
    assert resultado.mesas == [mesa_de_quatro]


def test_fechado_nao_consulta_mesa_e_diz_o_motivo(restaurante, aberto_no_sabado, mesa_de_quatro):
    """Fechado e tudo reservado sao respostas diferentes para o cliente."""
    resultado = services.disponibilidade(restaurante.pk, as_horas(SABADO, 15), pessoas=4)

    assert not resultado.aberto
    assert not resultado.livre
    assert resultado.mesas == []


def test_mesa_reservada_sai_da_disponibilidade(restaurante, aberto_no_sabado, mesa_de_quatro):
    reservar(restaurante, mesa_de_quatro, as_horas(SABADO, 20))

    resultado = services.disponibilidade(restaurante.pk, as_horas(SABADO, 21), pessoas=4)

    assert resultado.aberto
    assert not resultado.livre


def test_reserva_do_balcao_conta_igual(restaurante, aberto_no_sabado, mesa_de_quatro):
    """Reserva sem contato (telefone, balcao) e a mesma ocupacao.

    Se a disponibilidade so contasse o que veio pelo WhatsApp, o bot venderia
    uma mesa que a equipe ja prometeu.
    """
    reservar(restaurante, mesa_de_quatro, as_horas(SABADO, 20), contato=None)

    assert not services.disponibilidade(restaurante.pk, as_horas(SABADO, 20), 2).livre


def test_mesa_pequena_nao_serve_grupo_grande(restaurante, aberto_no_sabado):
    Mesa.objects.create(tenant=restaurante, nome="Bistro", capacidade=2)

    resultado = services.disponibilidade(restaurante.pk, as_horas(SABADO, 20), pessoas=4)

    assert resultado.mesas == []


def test_mesa_inativa_sai_da_disponibilidade(restaurante, aberto_no_sabado):
    """Mesa em reforma nao e apagada; sai da conta pelo `ativa`."""
    Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4, ativa=False)

    assert services.disponibilidade(restaurante.pk, as_horas(SABADO, 20), 4).mesas == []


def test_reserva_cancelada_devolve_a_mesa(restaurante, aberto_no_sabado, mesa_de_quatro):
    reserva = reservar(restaurante, mesa_de_quatro, as_horas(SABADO, 20))
    reserva.status = Reserva.Status.CANCELADA
    reserva.save(update_fields=["status", "atualizado_em"])

    assert services.disponibilidade(restaurante.pk, as_horas(SABADO, 20), 4).livre


def test_encostar_no_fim_da_reserva_anterior_esta_livre(
    restaurante, aberto_no_sabado, mesa_de_quatro
):
    """Semiaberto: quem reservou as 19h solta a mesa as 21h."""
    reservar(restaurante, mesa_de_quatro, as_horas(SABADO, 19))

    assert services.disponibilidade(restaurante.pk, as_horas(SABADO, 21), 4).livre


def test_nao_ve_mesa_do_vizinho(restaurante, clinica, aberto_no_sabado):
    HorarioFuncionamento.objects.create(
        tenant=clinica, dia_semana=SABADO.weekday(), abre=time(19, 0), fecha=time(23, 0)
    )
    Mesa.objects.create(tenant=clinica, nome="Mesa 1", capacidade=4)

    assert services.disponibilidade(restaurante.pk, as_horas(SABADO, 20), 4).mesas == []


def test_horarios_livres_cobre_a_faixa_de_meia_em_meia_hora(
    restaurante, aberto_no_sabado, mesa_de_quatro
):
    livres = services.horarios_livres(restaurante.pk, SABADO, pessoas=4, agora=as_horas(SABADO, 10))

    assert livres[0] == as_horas(SABADO, 19)
    assert livres[-1] == as_horas(SABADO, 22, 30)
    assert len(livres) == 8


def test_horarios_livres_tira_o_que_a_reserva_ocupa(restaurante, aberto_no_sabado, mesa_de_quatro):
    """Com uma mesa so, a reserva das 20h derruba tudo que se cruza com ela.

    Um grupo que sentasse as 19h ficaria ate as 21h, dentro da reserva das
    20h; sobram os horarios que comecam depois que ela solta a mesa.
    """
    reservar(restaurante, mesa_de_quatro, as_horas(SABADO, 20))

    livres = services.horarios_livres(restaurante.pk, SABADO, pessoas=4, agora=as_horas(SABADO, 10))

    assert livres == [as_horas(SABADO, 22), as_horas(SABADO, 22, 30)]


def test_horarios_livres_ignora_horario_que_ja_passou(
    restaurante, aberto_no_sabado, mesa_de_quatro
):
    """Para "tem mesa hoje?" a resposta nao pode sugerir o inicio da noite."""
    livres = services.horarios_livres(restaurante.pk, SABADO, pessoas=4, agora=as_horas(SABADO, 21))

    assert livres == [as_horas(SABADO, 21, 30), as_horas(SABADO, 22), as_horas(SABADO, 22, 30)]


def test_horarios_livres_usa_a_outra_mesa_quando_uma_esta_ocupada(
    restaurante, aberto_no_sabado, mesa_de_quatro
):
    outra = Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    reservar(restaurante, mesa_de_quatro, as_horas(SABADO, 20))

    livres = services.horarios_livres(restaurante.pk, SABADO, pessoas=4, agora=as_horas(SABADO, 10))

    assert as_horas(SABADO, 20) in livres
    assert outra.pk


def test_horarios_livres_inclui_a_madrugada_da_faixa_da_vespera(restaurante, mesa_de_quatro):
    """Bar aberto 22h as 2h: a madrugada de sabado e horario de sexta.

    Sem ler a faixa da vespera, `horarios_livres` nunca sugeriria 00h30
    embora `esta_aberto` dissesse que esta aberto — as duas respondem a mesma
    pergunta e nao podem discordar.
    """
    sexta = SABADO - timedelta(days=1)
    for dia in (sexta, SABADO):
        HorarioFuncionamento.objects.create(
            tenant=restaurante, dia_semana=dia.weekday(), abre=time(22, 0), fecha=time(2, 0)
        )

    livres = services.horarios_livres(restaurante.pk, SABADO, pessoas=4, agora=as_horas(sexta, 10))

    assert livres[0] == as_horas(SABADO, 0)
    assert as_horas(SABADO, 1, 30) in livres
    assert livres[-1] == as_horas(SABADO, 23, 30)


def test_reserva_da_vespera_que_invade_a_madrugada_ocupa(restaurante, mesa_de_quatro):
    """A reserva de sexta 23h30 segura a mesa ate 01h30 de sabado.

    A folga de um dia na consulta existe por isso: sem ela o slot da
    madrugada veria a mesa livre.
    """
    sexta = SABADO - timedelta(days=1)
    for dia in (sexta, SABADO):
        HorarioFuncionamento.objects.create(
            tenant=restaurante, dia_semana=dia.weekday(), abre=time(22, 0), fecha=time(2, 0)
        )
    reservar(restaurante, mesa_de_quatro, as_horas(sexta, 23, 30))

    livres = services.horarios_livres(restaurante.pk, SABADO, pessoas=4, agora=as_horas(sexta, 10))

    assert as_horas(SABADO, 0) not in livres
    assert as_horas(SABADO, 1) not in livres
    assert as_horas(SABADO, 1, 30) in livres


def test_horarios_livres_sem_horario_cadastrado_e_vazio(restaurante, mesa_de_quatro):
    assert services.horarios_livres(restaurante.pk, SABADO, 4) == []


def test_horarios_livres_sem_mesa_cadastrada_e_vazio(restaurante, aberto_no_sabado):
    assert services.horarios_livres(restaurante.pk, SABADO, 4) == []


def test_horarios_livres_cabe_em_tres_consultas(
    restaurante, aberto_no_sabado, mesa_de_quatro, django_assert_num_queries
):
    """Faixas, mesas e reservas do dia. Mais que isso e consulta por slot."""
    reservar(restaurante, mesa_de_quatro, as_horas(SABADO, 20))

    with django_assert_num_queries(3):
        services.horarios_livres(restaurante.pk, SABADO, 4, agora=as_horas(SABADO, 10))
