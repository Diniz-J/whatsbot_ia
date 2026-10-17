"""Mesa, reserva e as garantias que o banco tem que dar.

Duas pessoas na mesma mesa no mesmo horario e o erro que aparece na porta do
restaurante, com o cliente na frente. Por isso a sobreposicao e recusada pelo
banco, e nao so pela validacao em Python: dois workers gravando ao mesmo tempo
passariam por qualquer checagem feita antes do INSERT.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.conversas.models import Contato
from apps.reservas.models import DURACAO_PADRAO, Mesa, Reserva


@pytest.fixture
def mesa(restaurante):
    return Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)


@pytest.fixture
def amanha():
    return (timezone.now() + timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)


def reservar(tenant, mesa, inicio, **extras):
    return Reserva.objects.create(
        tenant=tenant,
        mesa=mesa,
        nome=extras.pop("nome", "Ana"),
        pessoas=extras.pop("pessoas", 2),
        inicio=inicio,
        **extras,
    )


def test_fim_vazio_usa_a_duracao_padrao(restaurante, mesa, amanha):
    """O fim fica gravado, entao mudar o padrao amanha nao reescreve o antigo."""
    reserva = reservar(restaurante, mesa, amanha)

    assert reserva.fim - reserva.inicio == DURACAO_PADRAO


def test_banco_recusa_sobreposicao_na_mesma_mesa(restaurante, mesa, amanha):
    reservar(restaurante, mesa, amanha)

    with pytest.raises(IntegrityError), transaction.atomic():
        reservar(restaurante, mesa, amanha + timedelta(minutes=30), nome="Bruno")


def test_reserva_encostada_no_fim_da_anterior_passa(restaurante, mesa, amanha):
    """Intervalo semiaberto: a mesa vira as 22h para quem reservou as 22h."""
    primeira = reservar(restaurante, mesa, amanha)

    segunda = reservar(restaurante, mesa, primeira.fim, nome="Bruno")

    assert segunda.inicio == primeira.fim


def test_cancelar_libera_o_horario(restaurante, mesa, amanha):
    """Cancelada nao segura mesa; se segurasse, desmarcar nao devolveria o lugar."""
    primeira = reservar(restaurante, mesa, amanha)
    primeira.status = Reserva.Status.CANCELADA
    primeira.save(update_fields=["status", "atualizado_em"])

    segunda = reservar(restaurante, mesa, amanha, nome="Bruno")

    assert segunda.pk


def test_nao_compareceu_tambem_libera(restaurante, mesa, amanha):
    primeira = reservar(restaurante, mesa, amanha)
    primeira.status = Reserva.Status.NAO_COMPARECEU
    primeira.save(update_fields=["status", "atualizado_em"])

    assert reservar(restaurante, mesa, amanha, nome="Bruno").pk


def test_compareceu_continua_segurando(restaurante, mesa, amanha):
    """Quem sentou ocupa a mesa tanto quanto quem confirmou."""
    primeira = reservar(restaurante, mesa, amanha)
    primeira.status = Reserva.Status.COMPARECEU
    primeira.save(update_fields=["status", "atualizado_em"])

    with pytest.raises(IntegrityError), transaction.atomic():
        reservar(restaurante, mesa, amanha + timedelta(minutes=30), nome="Bruno")


def test_mesa_diferente_no_mesmo_horario_passa(restaurante, mesa, amanha):
    outra = Mesa.objects.create(tenant=restaurante, nome="Mesa 2", capacidade=2)
    reservar(restaurante, mesa, amanha)

    assert reservar(restaurante, outra, amanha, nome="Bruno").pk


def test_clean_recusa_mais_pessoas_que_a_capacidade(restaurante, mesa, amanha):
    reserva = Reserva(tenant=restaurante, mesa=mesa, nome="Ana", pessoas=6, inicio=amanha)

    with pytest.raises(ValidationError) as erro:
        reserva.full_clean()

    assert "pessoas" in erro.value.message_dict


def test_clean_recusa_mesa_de_outro_cliente(restaurante, clinica, amanha):
    alheia = Mesa.objects.create(tenant=clinica, nome="Mesa 1", capacidade=4)
    reserva = Reserva(tenant=restaurante, mesa=alheia, nome="Ana", pessoas=2, inicio=amanha)

    with pytest.raises(ValidationError) as erro:
        reserva.full_clean()

    assert "mesa" in erro.value.message_dict


def test_clean_recusa_contato_de_outro_cliente(restaurante, clinica, mesa, amanha):
    alheio = Contato.objects.create(tenant=clinica, telefone="5511888888888")
    reserva = Reserva(
        tenant=restaurante, mesa=mesa, contato=alheio, nome="Ana", pessoas=2, inicio=amanha
    )

    with pytest.raises(ValidationError) as erro:
        reserva.full_clean()

    assert "contato" in erro.value.message_dict


def test_clean_recusa_fim_antes_do_inicio(restaurante, mesa, amanha):
    reserva = Reserva(
        tenant=restaurante,
        mesa=mesa,
        nome="Ana",
        pessoas=2,
        inicio=amanha,
        fim=amanha - timedelta(hours=1),
    )

    with pytest.raises(ValidationError) as erro:
        reserva.full_clean()

    assert "fim" in erro.value.message_dict


def test_clean_avisa_da_sobreposicao_no_formulario(restaurante, mesa, amanha):
    """O banco ja recusaria, mas como IntegrityError no meio do request.

    A checagem no `clean` devolve o erro no campo, que e o que o back-office
    sabe mostrar.
    """
    reservar(restaurante, mesa, amanha)
    nova = Reserva(
        tenant=restaurante, mesa=mesa, nome="Bruno", pessoas=2, inicio=amanha + timedelta(hours=1)
    )

    with pytest.raises(ValidationError) as erro:
        nova.full_clean()

    assert "inicio" in erro.value.message_dict


def test_editar_a_propria_reserva_nao_conflita_consigo(restaurante, mesa, amanha):
    reserva = reservar(restaurante, mesa, amanha)
    reserva.pessoas = 3

    reserva.full_clean()
    reserva.save()

    assert Reserva.objects.get(pk=reserva.pk).pessoas == 3


def test_mesa_unica_por_nome_no_cliente(restaurante, mesa):
    with pytest.raises(IntegrityError), transaction.atomic():
        Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=2)


def test_mesmo_nome_de_mesa_em_clientes_diferentes_passa(restaurante, clinica, mesa):
    assert Mesa.objects.create(tenant=clinica, nome="Mesa 1", capacidade=4).pk
