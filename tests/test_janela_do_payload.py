"""A janela de 24h e do relogio real, nao do que o payload diz.

O instante que move a janela sai do `timestamp` da Cloud API e nada o
limitava. Com um valor no futuro — relogio torto do lado de la, ou payload
montado — a janela ficaria "aberta" por todo esse tempo: o bot mandaria texto
livre, a Meta recusaria por estar fora das 24h de verdade, e o cliente nao
receberia nada.

E o estrago seria permanente, nao temporario: a janela so anda para frente de
proposito, entao o instante no futuro nao e corrigido pela mensagem real
seguinte.
"""

from datetime import timedelta

from django.utils import timezone

from apps.conversas.models import EventoWebhook
from apps.core.services import processar_evento
from tests.payloads_whatsapp import payload_mensagem


def em_segundos(quando) -> int:
    return int(quando.timestamp())


def processar(tenant, id_mensagem, timestamp=None):
    evento = EventoWebhook.objects.create(
        tenant=tenant,
        id_mensagem=id_mensagem,
        payload=payload_mensagem(id_mensagem=id_mensagem, timestamp=timestamp),
    )
    (mensagem,) = processar_evento(evento)
    return mensagem


def test_timestamp_no_futuro_nao_estica_a_janela(restaurante):
    um_mes_a_frente = em_segundos(timezone.now() + timedelta(days=30))

    conversa = processar(restaurante, "wamid.FUTURO", um_mes_a_frente).conversa

    assert conversa.ultima_entrada_em <= timezone.now()
    assert conversa.janela_aberta() is True
    assert conversa.janela_aberta(timezone.now() + timedelta(hours=25)) is False


def test_timestamp_no_futuro_nao_tranca_a_conversa(restaurante):
    """A mensagem real seguinte nao desfaz o estrago, porque a janela so anda
    para frente. Por isso o limite tem que ser na gravacao, nao na leitura."""
    processar(restaurante, "wamid.FUTURO", em_segundos(timezone.now() + timedelta(days=30)))

    conversa = processar(restaurante, "wamid.AGORA").conversa

    assert conversa.janela_aberta(timezone.now() + timedelta(hours=25)) is False


def test_timestamp_no_passado_nao_anda_para_tras(restaurante):
    """O limite novo nao pode custar a regra que ja existia: entrega fora de
    ordem, ou reprocessada, nao encurta janela que uma mensagem mais nova
    abriu."""
    processar(restaurante, "wamid.NOVA")
    ontem = em_segundos(timezone.now() - timedelta(days=1))

    conversa = processar(restaurante, "wamid.VELHA", ontem).conversa

    assert conversa.janela_aberta() is True
