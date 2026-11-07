"""A ferramenta de fechar reserva, vista pelo modelo.

O texto que volta e tudo que o modelo tem para decidir o que afirmar ao
cliente. "CONFIRMADA" e "NAO FECHADA" precisam ser inconfundiveis, e cada
motivo de recusa precisa chegar separado: "ja passou" nao e "esta cheio".
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.catalogo.models import HorarioFuncionamento
from apps.conversas.models import Contato
from apps.reservas.models import Mesa, Reserva
from apps.responder.llm import ferramentas

FUSO = ZoneInfo("America/Sao_Paulo")


def proximo_sabado() -> date:
    hoje = timezone.localdate()
    return hoje + timedelta(days=(5 - hoje.weekday()) % 7 + 7)


SABADO = proximo_sabado()


@pytest.fixture(autouse=True)
def fuso_do_negocio(settings):
    settings.TIME_ZONE = "America/Sao_Paulo"
    timezone.activate(FUSO)
    yield
    timezone.deactivate()


@pytest.fixture
def contato(restaurante):
    return Contato.objects.create(
        tenant=restaurante, telefone="5511999999999", nome_perfil="Ana Souza"
    )


@pytest.fixture
def casa_aberta(restaurante):
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SABADO.weekday(), abre=time(19, 0), fecha=time(23, 0)
    )
    return Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)


def fechar(tenant, contato_id, hora="20:00", pessoas=4, **extras):
    argumentos = {"data_hora": f"{SABADO}T{hora}", "pessoas": pessoas, **extras}
    return ferramentas.executar("fechar_reserva", argumentos, tenant.pk, contato_id=contato_id)


def test_confirma_com_os_dados_que_o_modelo_pode_repetir(restaurante, contato, casa_aberta):
    saida = fechar(restaurante, contato.pk)

    assert "RESERVA CONFIRMADA" in saida
    assert f"{SABADO:%d/%m} as 20:00" in saida
    assert "4 pessoas" in saida
    assert "Ana Souza" in saida
    assert Reserva.objects.count() == 1


def test_segunda_chamada_avisa_que_ja_estava_fechada(restaurante, contato, casa_aberta):
    """Sem o aviso o modelo diria "fechei" duas vezes para o mesmo cliente."""
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    fechar(restaurante, contato.pk)

    saida = fechar(restaurante, contato.pk)

    assert "ja estava fechada" in saida
    assert Reserva.objects.count() == 1


def test_a_hora_dita_ao_cliente_esta_no_fuso_do_negocio(restaurante, contato, casa_aberta):
    """O banco guarda em UTC, e reserva lida de volta chega adiantada.

    O caminho idempotente le do banco, entao dizia "as 23:00" para quem
    reservou as 20:00. A hora errada chegava ao cliente, nao ao log.
    """
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    nova = fechar(restaurante, contato.pk)
    do_banco = fechar(restaurante, contato.pk)

    assert "as 20:00" in nova
    assert "as 20:00" in do_banco


def test_sem_contato_no_contexto_nao_afirma_que_fechou(restaurante, casa_aberta):
    """Caminho que nao existe no atendimento normal; se acontecer, o modelo
    tem que oferecer uma pessoa em vez de dizer que fechou."""
    saida = fechar(restaurante, None)

    assert "NAO FECHADA" in saida
    assert not Reserva.objects.exists()


def test_fechado_sem_mesa_e_passado_sao_recusas_diferentes(restaurante, contato, casa_aberta):
    """O cliente ouve coisa diferente em cada uma."""
    ontem = timezone.localdate() - timedelta(days=1)
    passado = ferramentas.executar(
        "fechar_reserva",
        {"data_hora": f"{ontem}T20:00", "pessoas": 4},
        restaurante.pk,
        contato_id=contato.pk,
    )
    fora_do_horario = fechar(restaurante, contato.pk, hora="15:00")

    outro = Contato.objects.create(tenant=restaurante, telefone="5511777777777")
    fechar(restaurante, outro.pk)
    cheio = fechar(restaurante, contato.pk)

    assert "ja passou" in passado
    assert "nao ha atendimento" in fora_do_horario
    assert "nao ha mesa livre" in cheio
    for saida in (passado, fora_do_horario, cheio):
        assert "NAO FECHADA" in saida
        assert "CONFIRMADA" not in saida


def test_recusa_de_limite_manda_para_a_equipe(restaurante, contato, casa_aberta):
    from apps.reservas import services

    for numero in range(services.MAX_RESERVAS_ABERTAS):
        dia = SABADO + timedelta(days=7 * numero)
        HorarioFuncionamento.objects.get_or_create(
            tenant=restaurante, dia_semana=dia.weekday(), abre=time(19, 0), fecha=time(23, 0)
        )
        Mesa.objects.create(tenant=restaurante, nome=f"Extra {numero}", capacidade=4)
        services.criar_reserva(
            restaurante.pk,
            contato.pk,
            datetime.combine(dia, time(20, 0), tzinfo=FUSO),
            2,
        )

    distante = SABADO + timedelta(days=28)
    saida = ferramentas.executar(
        "fechar_reserva",
        {"data_hora": f"{distante}T20:00", "pessoas": 2},
        restaurante.pk,
        contato_id=contato.pk,
    )

    assert "maximo de reservas futuras" in saida
    assert "equipe" in saida


def test_data_invalida_volta_como_resultado(restaurante, contato, casa_aberta):
    saida = ferramentas.executar(
        "fechar_reserva",
        {"data_hora": "sabado que vem", "pessoas": 4},
        restaurante.pk,
        contato_id=contato.pk,
    )

    assert "invalidas" in saida
    assert not Reserva.objects.exists()


def test_observacao_do_cliente_tem_teto(restaurante, contato, casa_aberta):
    """O conteudo e ditado pelo cliente final; sem teto entraria cru no banco."""
    fechar(restaurante, contato.pk, observacao="x" * 1000)

    assert len(Reserva.objects.get().observacao) == ferramentas.LIMITE_DA_OBSERVACAO


def test_todo_motivo_de_recusa_tem_texto(restaurante):
    """Motivo novo no servico sem frase aqui viraria KeyError no atendimento."""
    from apps.reservas import services

    for motivo in services.Motivo:
        assert motivo in ferramentas.RECUSA


def cancelar(tenant, contato_id):
    return ferramentas.executar("cancelar_reserva", {}, tenant.pk, contato_id=contato_id)


def remarcar(tenant, contato_id, hora="21:30", **extras):
    argumentos = {"nova_data_hora": f"{SABADO}T{hora}", **extras}
    return ferramentas.executar("remarcar_reserva", argumentos, tenant.pk, contato_id=contato_id)


def test_cancelamento_diz_qual_reserva_foi(restaurante, contato, casa_aberta):
    """Quem tem duas reservas ouviria "cancelado" e nao saberia qual."""
    fechar(restaurante, contato.pk)

    saida = cancelar(restaurante, contato.pk)

    assert "RESERVA CANCELADA" in saida
    assert f"{SABADO:%d/%m} as 20:00" in saida
    assert Reserva.objects.get().status == Reserva.Status.CANCELADA


def test_cancelar_sem_reserva_nao_diz_que_cancelou(restaurante, contato, casa_aberta):
    saida = cancelar(restaurante, contato.pk)

    assert "NAO CANCELADA" in saida
    assert "nao tem reserva futura" in saida


def test_remarcacao_diz_a_hora_nova(restaurante, contato, casa_aberta):
    fechar(restaurante, contato.pk)

    saida = remarcar(restaurante, contato.pk)

    assert "RESERVA REMARCADA" in saida
    assert f"{SABADO:%d/%m} as 21:30" in saida
    assert Reserva.objects.count() == 1


def test_remarcar_pode_mudar_o_grupo(restaurante, contato, casa_aberta):
    fechar(restaurante, contato.pk, pessoas=2)

    saida = remarcar(restaurante, contato.pk, pessoas=4)

    assert "4 pessoas" in saida


def test_recusa_de_remarcar_nao_diz_nao_fechada(restaurante, contato, casa_aberta):
    """O verbo errado faria o modelo dizer a coisa errada ao cliente."""
    fechar(restaurante, contato.pk)

    saida = remarcar(restaurante, contato.pk, hora="15:00")

    assert "NAO REMARCADA" in saida
    assert "NAO FECHADA" not in saida
    assert "nao ha atendimento" in saida


def test_sem_contato_nao_cancela_nem_remarca(restaurante, contato, casa_aberta):
    fechar(restaurante, contato.pk)

    sem_cancelar = cancelar(restaurante, None)
    sem_remarcar = remarcar(restaurante, None)

    assert "NAO CANCELADA" in sem_cancelar
    assert "NAO REMARCADA" in sem_remarcar
    assert Reserva.objects.get().status == Reserva.Status.CONFIRMADA


def test_data_invalida_na_remarcacao_volta_como_resultado(restaurante, contato, casa_aberta):
    fechar(restaurante, contato.pk)

    saida = ferramentas.executar(
        "remarcar_reserva",
        {"nova_data_hora": "sabado que vem"},
        restaurante.pk,
        contato_id=contato.pk,
    )

    assert "invalidas" in saida
    assert Reserva.objects.get().inicio is not None
