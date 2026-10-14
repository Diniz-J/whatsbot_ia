"""Recebimento de entrega: assinatura, dono do numero e idempotencia."""

import pytest

from apps.conversas.models import Contato, Conversa, EventoWebhook, Mensagem
from tests.payloads_whatsapp import SEGREDO, corpo_e_assinatura, payload_mensagem, payload_status

URL = "/webhook/whatsapp/"


@pytest.fixture(autouse=True)
def segredo(settings):
    settings.WHATSAPP_APP_SECRET = SEGREDO


def entregar(client, payload, assinatura=None, segredo=SEGREDO):
    corpo, calculada = corpo_e_assinatura(payload, segredo)
    return client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura or calculada},
    )


def test_recusa_sem_assinatura(client, restaurante):
    corpo, _ = corpo_e_assinatura(payload_mensagem())

    resposta = client.post(URL, data=corpo, content_type="application/json")

    assert resposta.status_code == 403
    assert not EventoWebhook.objects.exists()


def test_recusa_assinatura_de_outro_segredo(client, restaurante):
    resposta = entregar(client, payload_mensagem(), segredo="segredo-errado")

    assert resposta.status_code == 403
    assert not EventoWebhook.objects.exists()


def test_recusa_assinatura_que_nao_cobre_o_corpo_enviado(client, restaurante):
    """Assinatura valida de OUTRO payload nao serve para este."""
    _, assinatura_de_outro = corpo_e_assinatura(payload_mensagem(texto="payload diferente"))

    resposta = entregar(client, payload_mensagem(texto="este aqui"), assinatura=assinatura_de_outro)

    assert resposta.status_code == 403


def test_recusa_quando_nenhum_segredo_esta_configurado(client, restaurante, settings):
    settings.WHATSAPP_APP_SECRET = ""

    assert entregar(client, payload_mensagem()).status_code == 403


def test_recusa_numero_de_outra_instancia(client, restaurante):
    """A assinatura prova que veio da Meta, nao que era para este cliente."""
    resposta = entregar(client, payload_mensagem(phone_number_id="999"))

    assert resposta.status_code == 403
    assert not EventoWebhook.objects.exists()


def test_grava_e_processa_mensagem_do_proprio_numero(
    client, restaurante, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        resposta = entregar(client, payload_mensagem())

    assert resposta.status_code == 200

    evento = EventoWebhook.objects.get()
    assert evento.tenant_id == restaurante.id
    assert evento.id_mensagem == "wamid.AAA"
    assert evento.processado_em is not None

    mensagem = Mensagem.objects.get()
    assert mensagem.direcao == Mensagem.Direcao.ENTRADA
    assert mensagem.texto == "oi, qual o horario de voces?"
    assert mensagem.tenant_id == restaurante.id

    contato = Contato.objects.get()
    assert contato.telefone == "5511999999999"
    assert contato.nome_perfil == "Ze"

    conversa = Conversa.objects.get()
    assert conversa.ultima_entrada_em is not None
    assert conversa.janela_aberta()


def test_reenvio_da_mesma_mensagem_nao_duplica(
    client, restaurante, django_capture_on_commit_callbacks
):
    """A Meta reenvia quando nao recebe 200 rapido (invariante 4)."""
    for _ in range(2):
        with django_capture_on_commit_callbacks(execute=True):
            assert entregar(client, payload_mensagem()).status_code == 200

    assert EventoWebhook.objects.count() == 1
    assert Mensagem.objects.count() == 1
    assert Contato.objects.count() == 1


def test_segunda_mensagem_do_mesmo_contato_reusa_conversa(
    client, restaurante, django_capture_on_commit_callbacks
):
    for id_mensagem in ("wamid.AAA", "wamid.BBB"):
        with django_capture_on_commit_callbacks(execute=True):
            entregar(client, payload_mensagem(id_mensagem=id_mensagem))

    assert Mensagem.objects.count() == 2
    assert Conversa.objects.count() == 1
    assert Contato.objects.count() == 1


def test_status_de_envio_nao_vira_mensagem(client, restaurante, django_capture_on_commit_callbacks):
    """Status chega na mesma URL e pertence ao lado de saida."""
    with django_capture_on_commit_callbacks(execute=True):
        resposta = entregar(client, payload_status())

    assert resposta.status_code == 200
    assert not EventoWebhook.objects.exists()
    assert not Mensagem.objects.exists()


def test_corpo_que_nao_e_json_e_recusado(client, restaurante):
    import hashlib
    import hmac

    corpo = b"nao sou json"
    digest = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()

    resposta = client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": f"sha256={digest}"},
    )

    assert resposta.status_code == 400


def test_mensagem_nao_textual_e_gravada_com_o_tipo(
    client, restaurante, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        entregar(client, payload_mensagem(tipo="audio"))

    mensagem = Mensagem.objects.get()
    assert mensagem.tipo == "audio"
    assert mensagem.texto == ""


def test_entrega_com_duas_mensagens_nao_duplica(
    client, restaurante, django_capture_on_commit_callbacks
):
    """Cada mensagem gera o seu evento, e cada evento grava somente a sua.

    Os dois eventos guardam o payload inteiro, entao sem o filtro por id o
    primeiro gravaria tambem a mensagem do segundo, e o segundo a repetiria.
    """
    payload = payload_mensagem()
    payload["entry"][0]["changes"][0]["value"]["messages"].append(
        {
            "from": "5511999999999",
            "id": "wamid.BBB",
            "timestamp": "1760000001",
            "type": "text",
            "text": {"body": "e aberto domingo?"},
        }
    )

    with django_capture_on_commit_callbacks(execute=True):
        assert entregar(client, payload).status_code == 200

    assert EventoWebhook.objects.count() == 2
    assert sorted(Mensagem.objects.values_list("id_externo", flat=True)) == [
        "wamid.AAA",
        "wamid.BBB",
    ]
    assert Conversa.objects.count() == 1
