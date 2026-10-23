"""As ferramentas de disponibilidade vistas pelo modelo.

O que importa aqui e o texto que volta: e a unica coisa que o modelo le, e e
dele que sai a frase que o cliente recebe. "Fechado" e "cheio" precisam
chegar separados, e argumento invalido precisa voltar como resultado em vez de
derrubar o atendimento.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.catalogo.models import HorarioFuncionamento
from apps.reservas.models import Mesa, Reserva
from apps.responder.llm import ferramentas

FUSO = ZoneInfo("America/Sao_Paulo")


def proximo_sabado() -> date:
    """Um sabado no futuro, sempre.

    Data fixa no passado faria `horarios_livres` devolver lista vazia, porque
    ele descarta horario que ja passou — e o teste morreria sozinho com o
    tempo em vez de apontar um defeito.
    """
    hoje = timezone.localdate()
    return hoje + timedelta(days=(5 - hoje.weekday()) % 7 + 7)


SABADO = proximo_sabado()
DOMINGO = SABADO + timedelta(days=1)


@pytest.fixture(autouse=True)
def fuso_do_negocio(settings):
    settings.TIME_ZONE = "America/Sao_Paulo"
    timezone.activate(FUSO)
    yield
    timezone.deactivate()


@pytest.fixture
def restaurante_aberto(restaurante):
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SABADO.weekday(), abre=time(19, 0), fecha=time(23, 0)
    )
    Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)
    return restaurante


def executar(nome, argumentos, tenant):
    return ferramentas.executar(nome, argumentos, tenant.pk)


def test_diz_que_ha_mesa_e_que_a_reserva_nao_esta_feita(restaurante_aberto):
    """O texto que o modelo le nao pode deixar margem para prometer reserva."""
    saida = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{SABADO}T20:00", "pessoas": 4},
        restaurante_aberto,
    )

    assert "HA MESA" in saida
    assert f"{SABADO:%d/%m} as 20:00" in saida
    assert "NAO esta" in saida


def test_disponibilidade_manda_fechar_pela_ferramenta(restaurante_aberto):
    """Quem fecha e o bot, pela ferramenta — nao a equipe.

    Resultado de ferramenta pesa mais no modelo que o prompt de sistema. Com a
    frase antiga ("quem fecha e a equipe", de antes do `fechar_reserva`), ele
    prometeria ligacao da equipe justo depois de confirmar que ha mesa.
    """
    saida = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{SABADO}T20:00", "pessoas": 4},
        restaurante_aberto,
    )

    assert "fechar_reserva" in saida
    assert "equipe" not in saida


def test_fechado_e_cheio_sao_textos_diferentes(restaurante_aberto):
    """Juntar os dois faria o modelo escolher um motivo por conta propria."""
    fechado = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{SABADO}T15:00", "pessoas": 4},
        restaurante_aberto,
    )
    Reserva.objects.create(
        tenant=restaurante_aberto,
        mesa=Mesa.objects.get(tenant=restaurante_aberto),
        nome="Ana",
        pessoas=4,
        inicio=datetime.combine(SABADO, time(20, 0), tzinfo=FUSO),
    )
    cheio = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{SABADO}T20:00", "pessoas": 4},
        restaurante_aberto,
    )

    assert "FECHADO" in fechado
    assert "SEM MESA LIVRE" in cheio
    assert "FECHADO" not in cheio


def test_data_sem_fuso_e_lida_no_fuso_do_estabelecimento(restaurante_aberto):
    """O modelo manda hora local; interpretar em UTC jogaria a noite para o
    dia seguinte e a resposta sairia errada por tres horas."""
    saida = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{SABADO}T22:30", "pessoas": 4},
        restaurante_aberto,
    )

    assert "HA MESA" in saida


def test_data_invalida_volta_como_resultado_nao_como_excecao(restaurante_aberto):
    """Mesmo motivo da ferramenta inexistente: o modelo se corrige na volta."""
    saida = executar(
        "verificar_disponibilidade",
        {"data_hora": "sabado que vem", "pessoas": 4},
        restaurante_aberto,
    )

    assert "invalidas" in saida
    assert "ISO 8601" in saida


def test_pessoas_invalido_volta_como_resultado(restaurante_aberto):
    saida = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{SABADO}T20:00", "pessoas": 0},
        restaurante_aberto,
    )

    assert "pessoas invalido" in saida


def test_ferramenta_nao_ve_mesa_do_vizinho(restaurante_aberto, clinica):
    HorarioFuncionamento.objects.create(
        tenant=clinica, dia_semana=SABADO.weekday(), abre=time(19, 0), fecha=time(23, 0)
    )
    Mesa.objects.create(tenant=clinica, nome="Mesa 1", capacidade=4)

    saida = executar(
        "verificar_disponibilidade", {"data_hora": f"{SABADO}T20:00", "pessoas": 4}, clinica
    )
    Mesa.objects.filter(tenant=clinica).update(ativa=False)
    depois = executar(
        "verificar_disponibilidade", {"data_hora": f"{SABADO}T20:00", "pessoas": 4}, clinica
    )

    assert "HA MESA" in saida
    # A mesa do restaurante continua ativa, e nao pode salvar a clinica.
    assert "SEM MESA LIVRE" in depois


def test_horarios_livres_lista_as_horas(restaurante_aberto):
    saida = executar("horarios_livres", {"dia": f"{SABADO}", "pessoas": 4}, restaurante_aberto)

    assert "19:00" in saida
    assert "22:30" in saida


def test_horario_que_ja_passou_nao_recebe_resposta_de_disponibilidade(restaurante_aberto):
    """O servico responde por qualquer instante, inclusive passado, porque
    tambem serve consulta da equipe. Para o cliente final isso seria resposta
    certa para pergunta sem sentido."""
    ontem = timezone.localdate() - timedelta(days=1)

    saida = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{ontem}T20:00", "pessoas": 4},
        restaurante_aberto,
    )

    assert "ja passou" in saida
    assert "HA MESA" not in saida


def test_horarios_livres_sem_nada_e_explicito(restaurante_aberto):
    saida = executar("horarios_livres", {"dia": f"{DOMINGO}", "pessoas": 4}, restaurante_aberto)

    assert "Nenhum horario livre" in saida


def test_horarios_livres_tem_teto_de_tokens(restaurante):
    """Um dia inteiro de meia em meia hora passa de quarenta horarios, e cada
    um e token pago numa pergunta so."""
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SABADO.weekday(), abre=time(0, 0), fecha=time(23, 30)
    )
    Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)

    saida = executar("horarios_livres", {"dia": f"{SABADO}", "pessoas": 4}, restaurante)

    assert f"primeiros {ferramentas.LIMITE_DE_HORARIOS}" in saida
    assert saida.count(":") == ferramentas.LIMITE_DE_HORARIOS + 1


def test_dia_invalido_volta_como_resultado(restaurante_aberto):
    saida = executar("horarios_livres", {"dia": "amanha", "pessoas": 4}, restaurante_aberto)

    assert "Dia invalido" in saida


def test_duracao_informada_ao_modelo_vem_do_dado(restaurante_aberto):
    """A duracao que o modelo repete para o cliente sai do intervalo gravado,
    nao de um numero escrito no prompt."""
    saida = executar(
        "verificar_disponibilidade",
        {"data_hora": f"{SABADO}T20:00", "pessoas": 4},
        restaurante_aberto,
    )

    assert "por 2h" in saida
    assert timedelta(hours=2)
