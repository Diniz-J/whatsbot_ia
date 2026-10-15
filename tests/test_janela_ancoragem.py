"""De que instante a janela de 24h conta.

Ela precisa contar do momento que a Meta informa, e nao da hora em que
gravamos: numa reentrega atrasada a hora de gravacao esticaria a janela, e num
processamento fora de ordem ela andaria para tras.
"""

from datetime import UTC, datetime, timedelta

from django.utils import timezone

from apps.conversas.models import Conversa, EventoWebhook
from apps.core.services import processar_evento
from tests.payloads_whatsapp import payload_mensagem


def test_janela_conta_do_timestamp_da_meta(restaurante):
    enviada = timezone.now() - timedelta(hours=30)
    evento = EventoWebhook.objects.create(
        tenant=restaurante,
        id_mensagem="wamid.AAA",
        payload=payload_mensagem(timestamp=int(enviada.timestamp())),
    )

    processar_evento(evento)

    conversa = Conversa.objects.get()
    assert conversa.ultima_entrada_em == enviada.replace(microsecond=0).astimezone(UTC)
    # Mensagem de 30h atras, reentregue agora: a janela esta fechada.
    assert conversa.janela_aberta() is False


def test_janela_nao_anda_para_tras(restaurante):
    """Processar uma entrega antiga depois de uma nova nao pode encurtar a
    janela que a nova abriu."""
    agora = timezone.now()
    nova = EventoWebhook.objects.create(
        tenant=restaurante,
        id_mensagem="wamid.NOVA",
        payload=payload_mensagem(id_mensagem="wamid.NOVA", timestamp=int(agora.timestamp())),
    )
    antiga = EventoWebhook.objects.create(
        tenant=restaurante,
        id_mensagem="wamid.ANTIGA",
        payload=payload_mensagem(
            id_mensagem="wamid.ANTIGA",
            timestamp=int((agora - timedelta(hours=40)).timestamp()),
        ),
    )

    processar_evento(nova)
    processar_evento(antiga)

    conversa = Conversa.objects.get()
    assert conversa.ultima_entrada_em == agora.replace(microsecond=0).astimezone(UTC)
    assert conversa.janela_aberta() is True


def test_sem_timestamp_cai_na_hora_da_gravacao(restaurante):
    payload = payload_mensagem()
    del payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"]
    evento = EventoWebhook.objects.create(
        tenant=restaurante, id_mensagem="wamid.AAA", payload=payload
    )

    processar_evento(evento)

    assert Conversa.objects.get().ultima_entrada_em == evento.criado_em


def test_timestamp_invalido_cai_na_hora_da_gravacao(restaurante):
    payload = payload_mensagem()
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "ontem"
    evento = EventoWebhook.objects.create(
        tenant=restaurante, id_mensagem="wamid.AAA", payload=payload
    )

    processar_evento(evento)

    assert Conversa.objects.get().ultima_entrada_em == evento.criado_em


def test_datetime_da_meta_e_consciente_de_fuso(restaurante):
    """Epoch e UTC; comparar com `timezone.now()` exige datetime com tzinfo."""
    evento = EventoWebhook.objects.create(
        tenant=restaurante, id_mensagem="wamid.AAA", payload=payload_mensagem()
    )

    processar_evento(evento)

    assert Conversa.objects.get().ultima_entrada_em.tzinfo is not None
    assert isinstance(Conversa.objects.get().ultima_entrada_em, datetime)
