"""Fechar reserva: o primeiro caminho em que o atendimento escreve no banco.

O erro que importa aqui nao e tecnico, e o cliente aparecendo no sabado sem
mesa. Entao a disponibilidade e reconferida no fechamento, nunca assumida, e
chamar duas vezes nao gasta duas mesas.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.catalogo.models import HorarioFuncionamento
from apps.conversas.models import Contato
from apps.reservas import services
from apps.reservas.models import DURACAO_PADRAO, Mesa, Reserva

FUSO = ZoneInfo("America/Sao_Paulo")


def proximo_sabado() -> date:
    hoje = timezone.localdate()
    return hoje + timedelta(days=(5 - hoje.weekday()) % 7 + 7)


SABADO = proximo_sabado()


def as_horas(hora: int, minuto: int = 0, dia: date = SABADO) -> datetime:
    return datetime.combine(dia, time(hora, minuto), tzinfo=FUSO)


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


def fechar(restaurante, contato, hora=20, pessoas=2, **extras):
    return services.criar_reserva(restaurante.pk, contato.pk, as_horas(hora), pessoas, **extras)


def test_fecha_e_grava_a_reserva(restaurante, contato, casa_aberta):
    reserva, criada = fechar(restaurante, contato, pessoas=4)

    assert criada
    assert reserva.pk
    assert reserva.mesa == casa_aberta
    assert reserva.status == Reserva.Status.CONFIRMADA
    assert reserva.inicio == as_horas(20)
    assert reserva.fim == as_horas(20) + DURACAO_PADRAO
    assert reserva.contato == contato


def test_nome_vem_do_perfil_do_contato(restaurante, contato, casa_aberta):
    """A identidade e o telefone; o nome e rotulo para a equipe ler.

    Nao e argumento do modelo de proposito: menos coisa que ele pode errar.
    """
    reserva, _ = fechar(restaurante, contato)

    assert reserva.nome == "Ana Souza"


def test_sem_nome_no_perfil_cai_no_telefone(restaurante, casa_aberta):
    """O campo e obrigatorio no banco e alguns contatos escondem o perfil."""
    anonimo = Contato.objects.create(tenant=restaurante, telefone="5511888888888")

    reserva, _ = fechar(restaurante, anonimo)

    assert reserva.nome == "5511888888888"


def test_chamar_duas_vezes_nao_gasta_duas_mesas(restaurante, contato, casa_aberta):
    """O modelo pode pedir o fechamento duas vezes na mesma volta."""
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)

    primeira, criada_1 = fechar(restaurante, contato)
    segunda, criada_2 = fechar(restaurante, contato)

    assert criada_1 and not criada_2
    assert primeira.pk == segunda.pk
    assert Reserva.objects.count() == 1


def test_horario_sobreposto_devolve_a_reserva_que_ja_existe(restaurante, contato, casa_aberta):
    """Quem tem 20h e pede 20h30 continua querendo a mesma noite."""
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)

    primeira, _ = fechar(restaurante, contato, hora=20)
    segunda, criada = services.criar_reserva(restaurante.pk, contato.pk, as_horas(20, 30), 2)

    assert not criada
    assert segunda.pk == primeira.pk


def test_outra_noite_e_reserva_nova(restaurante, contato, casa_aberta):
    primeira, _ = fechar(restaurante, contato, hora=19)
    segunda, criada = services.criar_reserva(restaurante.pk, contato.pk, as_horas(22), 2)

    assert criada
    assert segunda.pk != primeira.pk


def test_recusa_horario_que_ja_passou(restaurante, contato, casa_aberta):
    ontem = timezone.localdate() - timedelta(days=1)

    with pytest.raises(services.ReservaRecusada) as erro:
        services.criar_reserva(restaurante.pk, contato.pk, as_horas(20, dia=ontem), 2)

    assert erro.value.motivo == services.Motivo.PASSADO


def test_recusa_fora_do_horario_de_funcionamento(restaurante, contato, casa_aberta):
    with pytest.raises(services.ReservaRecusada) as erro:
        fechar(restaurante, contato, hora=15)

    assert erro.value.motivo == services.Motivo.FECHADO


def test_recusa_quando_nao_ha_mesa_livre(restaurante, contato, casa_aberta):
    """Reconfere a disponibilidade no fechamento, nunca assume.

    E o caminho que mais doi se errar: o cliente aparece no sabado e nao tem
    mesa.
    """
    outro = Contato.objects.create(tenant=restaurante, telefone="5511777777777")
    fechar(restaurante, outro, hora=20)

    with pytest.raises(services.ReservaRecusada) as erro:
        fechar(restaurante, contato, hora=20)

    assert erro.value.motivo == services.Motivo.SEM_MESA


def test_recusa_grupo_maior_que_a_maior_mesa(restaurante, contato, casa_aberta):
    with pytest.raises(services.ReservaRecusada) as erro:
        fechar(restaurante, contato, pessoas=10)

    assert erro.value.motivo == services.Motivo.SEM_MESA


def test_escolhe_a_menor_mesa_que_serve(restaurante, contato, casa_aberta):
    """Queimar a mesa de seis num casal custa a mesa de seis."""
    Mesa.objects.create(tenant=restaurante, nome="Grande", capacidade=8)
    pequena = Mesa.objects.create(tenant=restaurante, nome="Bistro", capacidade=2)

    reserva, _ = fechar(restaurante, contato, pessoas=2)

    assert reserva.mesa == pequena


def test_teto_de_reservas_abertas_por_contato(restaurante, contato, casa_aberta):
    """Um numero de WhatsApp nao pode bloquear a noite inteira da casa."""
    for numero in range(services.MAX_RESERVAS_ABERTAS):
        Mesa.objects.create(tenant=restaurante, nome=f"Extra {numero}", capacidade=4)
        dia = SABADO + timedelta(days=7 * numero)
        HorarioFuncionamento.objects.get_or_create(
            tenant=restaurante,
            dia_semana=dia.weekday(),
            abre=time(19, 0),
            fecha=time(23, 0),
        )
        services.criar_reserva(restaurante.pk, contato.pk, as_horas(20, dia=dia), 2)

    with pytest.raises(services.ReservaRecusada) as erro:
        services.criar_reserva(
            restaurante.pk, contato.pk, as_horas(21, 30, dia=SABADO + timedelta(days=28)), 2
        )

    assert erro.value.motivo == services.Motivo.LIMITE


def test_reserva_cancelada_nao_conta_no_teto(restaurante, contato, casa_aberta):
    reserva, _ = fechar(restaurante, contato)
    reserva.status = Reserva.Status.CANCELADA
    reserva.save(update_fields=["status", "atualizado_em"])

    assert services.reservas_abertas(restaurante.pk, contato.pk) == []


def test_contato_de_outro_cliente_e_defeito_e_nao_recusa(restaurante, clinica, casa_aberta):
    """Nao chega aqui pelo caminho normal; se chegar, e bug, nao negocio."""
    alheio = Contato.objects.create(tenant=clinica, telefone="5511666666666")

    with pytest.raises(ValueError, match="nao e do cliente"):
        services.criar_reserva(restaurante.pk, alheio.pk, as_horas(20), 2)


def test_observacao_do_cliente_fica_gravada(restaurante, contato, casa_aberta):
    """Campo livre para a equipe ler, nao fato sobre preco ou mesa."""
    reserva, _ = fechar(restaurante, contato, observacao="aniversario, mesa perto da janela")

    assert "aniversario" in reserva.observacao


def test_reservas_abertas_lista_da_proxima_em_diante(restaurante, contato, casa_aberta):
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    tarde, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(22), 2)
    cedo, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)

    assert [r.pk for r in services.reservas_abertas(restaurante.pk, contato.pk)] == [
        cedo.pk,
        tarde.pk,
    ]


def test_cancela_a_proxima_e_diz_qual_foi(restaurante, contato, casa_aberta):
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    cedo, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)
    tarde, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(22), 2)

    cancelada = services.cancelar_reserva(restaurante.pk, contato.pk)

    assert cancelada.pk == cedo.pk
    assert cancelada.status == Reserva.Status.CANCELADA
    tarde.refresh_from_db()
    assert tarde.status == Reserva.Status.CONFIRMADA


def test_cancelar_libera_o_horario_para_outra_pessoa(restaurante, contato, casa_aberta):
    """Cancelada nao apaga a linha, mas solta a mesa: e o que `STATUS_OCUPAM` faz."""
    services.criar_reserva(restaurante.pk, contato.pk, as_horas(20), 2)
    services.cancelar_reserva(restaurante.pk, contato.pk)
    outro = Contato.objects.create(tenant=restaurante, telefone="5511777777777")

    reserva, criada = services.criar_reserva(restaurante.pk, outro.pk, as_horas(20), 2)

    assert criada
    assert Reserva.objects.count() == 2


def test_cancelar_sem_reserva_futura_e_recusa_explicita(restaurante, contato, casa_aberta):
    with pytest.raises(services.ReservaRecusada) as erro:
        services.cancelar_reserva(restaurante.pk, contato.pk)

    assert erro.value.motivo == services.Motivo.SEM_RESERVA


def test_remarcar_move_a_mesma_linha(restaurante, contato, casa_aberta):
    """Remarcar e uma reserva que mudou de hora, nao duas reservas."""
    original, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)

    movida = services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(21))

    assert movida.pk == original.pk
    assert movida.inicio == as_horas(21)
    assert movida.fim == as_horas(21) + DURACAO_PADRAO
    assert Reserva.objects.count() == 1


def test_remarcar_para_horario_proximo_nao_esbarra_em_si_mesma(restaurante, contato, casa_aberta):
    """Mover 20h para 20h30 na mesma mesa esbarraria na propria reserva.

    Sem tirar ela da conta de ocupacao, a resposta seria "sem mesa" por causa
    da reserva que se quer mover — com uma mesa so, nada seria remarcavel.
    """
    services.criar_reserva(restaurante.pk, contato.pk, as_horas(20), 2)

    movida = services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(20, 30))

    assert movida.inicio == as_horas(20, 30)
    assert movida.mesa == casa_aberta


def test_remarcar_mantem_a_mesa_quando_ela_continua_livre(restaurante, contato, casa_aberta):
    """Trocar de mesa sem precisar e confusao para a equipe."""
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    original, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)

    movida = services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(21, 30))

    assert movida.mesa == original.mesa


def test_remarcar_troca_de_mesa_quando_a_atual_esta_ocupada(restaurante, contato, casa_aberta):
    outra = Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)
    vizinho = Contato.objects.create(tenant=restaurante, telefone="5511777777777")
    tomada, _ = services.criar_reserva(restaurante.pk, vizinho.pk, as_horas(21, 30), 2)

    movida = services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(21, 30))

    assert tomada.mesa == casa_aberta
    assert movida.mesa == outra


def test_remarcar_muda_o_tamanho_do_grupo_quando_pedido(restaurante, contato, casa_aberta):
    """Quem remarca costuma dizer que o grupo mudou no mesmo pedido."""
    services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)

    movida = services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(21), pessoas=4)

    assert movida.pessoas == 4


def test_remarcar_recusa_grupo_que_nao_cabe(restaurante, contato, casa_aberta):
    services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)

    with pytest.raises(services.ReservaRecusada) as erro:
        services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(21), pessoas=10)

    assert erro.value.motivo == services.Motivo.SEM_MESA


def test_remarcar_recusa_fora_do_horario_e_no_passado(restaurante, contato, casa_aberta):
    services.criar_reserva(restaurante.pk, contato.pk, as_horas(20), 2)
    ontem = timezone.localdate() - timedelta(days=1)

    with pytest.raises(services.ReservaRecusada) as fechado:
        services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(15))
    with pytest.raises(services.ReservaRecusada) as passado:
        services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(20, dia=ontem))

    assert fechado.value.motivo == services.Motivo.FECHADO
    assert passado.value.motivo == services.Motivo.PASSADO


def test_remarcar_sem_reserva_futura_e_recusa_explicita(restaurante, contato, casa_aberta):
    with pytest.raises(services.ReservaRecusada) as erro:
        services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(21))

    assert erro.value.motivo == services.Motivo.SEM_RESERVA


def test_remarcar_nao_recusa_por_causa_do_teto(restaurante, contato, casa_aberta):
    """Mover nao cria reserva, entao o teto de reservas abertas nao se aplica."""
    for numero in range(services.MAX_RESERVAS_ABERTAS):
        dia = SABADO + timedelta(days=7 * numero)
        HorarioFuncionamento.objects.get_or_create(
            tenant=restaurante, dia_semana=dia.weekday(), abre=time(19, 0), fecha=time(23, 0)
        )
        Mesa.objects.create(tenant=restaurante, nome=f"Extra {numero}", capacidade=4)
        services.criar_reserva(restaurante.pk, contato.pk, as_horas(20, dia=dia), 2)

    movida = services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(21, 30))

    assert movida.inicio == as_horas(21, 30)


def test_remarcar_a_proxima_nao_toca_na_seguinte(restaurante, contato, casa_aberta):
    Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=4)
    cedo, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(19), 2)
    tarde, _ = services.criar_reserva(restaurante.pk, contato.pk, as_horas(22), 2)

    movida = services.remarcar_reserva(restaurante.pk, contato.pk, as_horas(19, 30))

    assert movida.pk == cedo.pk
    tarde.refresh_from_db()
    assert tarde.inicio == as_horas(22)
