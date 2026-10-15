"""Horario de funcionamento: a outra coisa que o bot nao pode inventar.

Datas fixas de proposito. 2026-10-16 e sexta (weekday 4) e 2026-10-17 e
sabado (weekday 5).
"""

from datetime import datetime, time

import pytest
from django.utils import timezone

from apps.catalogo.models import HorarioFuncionamento
from apps.catalogo.services import esta_aberto, horarios, proxima_abertura

SEXTA = 4
SABADO = 5
DOMINGO = 6


def local(ano, mes, dia, hora, minuto=0):
    return timezone.make_aware(datetime(ano, mes, dia, hora, minuto))


@pytest.fixture
def almoco_e_jantar(restaurante):
    """Sexta com fechamento no meio da tarde: o caso comum, nao a excecao."""
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SEXTA, abre=time(11, 30), fecha=time(15, 0)
    )
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SEXTA, abre=time(19, 0), fecha=time(23, 0)
    )


@pytest.fixture
def madrugada(restaurante):
    """Sexta das 18h as 2h: fecha depois da meia-noite."""
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SEXTA, abre=time(18, 0), fecha=time(2, 0)
    )


def test_data_de_referencia_e_sexta():
    """Se esta afirmacao cair, os outros testes deste arquivo mentem."""
    assert datetime(2026, 10, 16).weekday() == SEXTA
    assert datetime(2026, 10, 17).weekday() == SABADO


@pytest.mark.parametrize(
    "quando,esperado",
    [
        (local(2026, 10, 16, 12, 0), True),
        (local(2026, 10, 16, 11, 30), True),
        (local(2026, 10, 16, 15, 0), False),
        (local(2026, 10, 16, 17, 0), False),
        (local(2026, 10, 16, 20, 0), True),
        (local(2026, 10, 16, 23, 0), False),
        (local(2026, 10, 17, 12, 0), False),
    ],
)
def test_aberto_entre_as_faixas_do_dia(restaurante, almoco_e_jantar, quando, esperado):
    assert esta_aberto(restaurante.id, quando) is esperado


@pytest.mark.parametrize(
    "quando,esperado",
    [
        (local(2026, 10, 16, 19, 0), True),
        (local(2026, 10, 16, 23, 59), True),
        (local(2026, 10, 17, 0, 30), True),
        (local(2026, 10, 17, 1, 59), True),
        (local(2026, 10, 17, 2, 0), False),
        (local(2026, 10, 17, 10, 0), False),
        (local(2026, 10, 16, 17, 0), False),
    ],
)
def test_faixa_que_passa_da_meia_noite(restaurante, madrugada, quando, esperado):
    """Bar e pizzaria fecham depois da meia-noite; tratar como faixa invalida
    faria o bot dizer que esta fechado no horario de maior movimento."""
    assert esta_aberto(restaurante.id, quando) is esperado


def test_sem_horario_cadastrado_nunca_esta_aberto(restaurante):
    """Falha fechada: sem dado, o bot nao afirma que esta aberto."""
    assert esta_aberto(restaurante.id, local(2026, 10, 16, 12, 0)) is False


def test_horario_e_escopado_por_cliente(restaurante, clinica, almoco_e_jantar):
    assert esta_aberto(restaurante.id, local(2026, 10, 16, 12, 0)) is True
    assert esta_aberto(clinica.id, local(2026, 10, 16, 12, 0)) is False


def test_proxima_abertura_no_mesmo_dia(restaurante, almoco_e_jantar):
    """Fechado entre almoco e jantar: abre hoje mesmo as 19h."""
    assert proxima_abertura(restaurante.id, local(2026, 10, 16, 16, 0)) == local(
        2026, 10, 16, 19, 0
    )


def test_proxima_abertura_na_semana_seguinte(restaurante, almoco_e_jantar):
    """Sabado sem horario: a proxima sexta e a resposta."""
    assert proxima_abertura(restaurante.id, local(2026, 10, 17, 12, 0)) == local(
        2026, 10, 23, 11, 30
    )


def test_proxima_abertura_ignora_faixa_que_ja_passou(restaurante, almoco_e_jantar):
    assert proxima_abertura(restaurante.id, local(2026, 10, 16, 23, 30)) == local(
        2026, 10, 23, 11, 30
    )


def test_proxima_abertura_sem_horario_e_desconhecida(restaurante):
    """`None` em vez de um chute: quem responde diz que nao sabe."""
    assert proxima_abertura(restaurante.id, local(2026, 10, 16, 12, 0)) is None


def test_proxima_abertura_com_varios_dias_pega_o_mais_proximo(restaurante, almoco_e_jantar):
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=DOMINGO, abre=time(12, 0), fecha=time(16, 0)
    )

    assert proxima_abertura(restaurante.id, local(2026, 10, 17, 12, 0)) == local(
        2026, 10, 18, 12, 0
    )


def test_horarios_lista_em_ordem(restaurante, almoco_e_jantar):
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=DOMINGO, abre=time(12, 0), fecha=time(16, 0)
    )

    assert [(h.dia_semana, h.abre) for h in horarios(restaurante.id)] == [
        (SEXTA, time(11, 30)),
        (SEXTA, time(19, 0)),
        (DOMINGO, time(12, 0)),
    ]
