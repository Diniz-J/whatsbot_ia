"""Status que a Meta reporta sobre as mensagens que mandamos."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.canal.whatsapp.canal import CanalWhatsApp
from apps.conversas.models import CategoriaCobranca, Contato, Conversa, Mensagem
from apps.core.status import aplicar_status
from tests.payloads_whatsapp import payload_status


@pytest.fixture
def enviada(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )
    return Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        status=Mensagem.Status.ENVIADA,
        id_externo="wamid.SAIDA",
        texto="mesa reservada",
    )


def aplicar(restaurante, **kwargs):
    status = CanalWhatsApp().normalizar_status(payload_status(**kwargs))
    return aplicar_status(restaurante.id, status)


def test_entregue_avanca_o_status(restaurante, enviada):
    assert aplicar(restaurante, status="delivered") == 1

    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.ENTREGUE
    assert enviada.status_em is not None


def test_lida_avanca_de_entregue(restaurante, enviada):
    aplicar(restaurante, status="delivered")
    aplicar(restaurante, status="read")

    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.LIDA


def test_status_nao_anda_para_tras(restaurante, enviada):
    """`read` antes de `delivered` acontece; o segundo nao pode rebaixar."""
    aplicar(restaurante, status="read")
    aplicar(restaurante, status="delivered")

    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.LIDA


def test_falha_registra_o_motivo(restaurante, enviada):
    aplicar(restaurante, status="failed", erro=(131026, "Message undeliverable"))

    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.FALHOU
    assert "131026" in enviada.erro
    assert "undeliverable" in enviada.erro


def test_falha_depois_de_entregue_nao_apaga_a_entrega(restaurante, enviada):
    """Contradicao: o que aconteceu vale mais que o que chegou depois."""
    aplicar(restaurante, status="delivered")
    aplicar(restaurante, status="failed", erro=(1, "qualquer"))

    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.ENTREGUE


def test_categoria_de_cobranca_e_guardada(restaurante, enviada):
    aplicar(restaurante, status="sent", categoria="utility", cobravel=True)

    enviada.refresh_from_db()
    assert enviada.categoria_cobranca == CategoriaCobranca.UTILITY
    assert enviada.cobravel is True


def test_callback_sem_preco_nao_apaga_a_categoria(restaurante, enviada):
    """O preco vem no primeiro status; os seguintes podem vir sem ele."""
    aplicar(restaurante, status="sent", categoria="marketing")
    aplicar(restaurante, status="read", categoria=None)

    enviada.refresh_from_db()
    assert enviada.categoria_cobranca == CategoriaCobranca.MARKETING


def test_status_de_mensagem_desconhecida_e_ignorado(restaurante, enviada):
    assert aplicar(restaurante, id_mensagem="wamid.DE_OUTRO") == 0

    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.ENVIADA


def test_status_nao_alcanca_mensagem_de_outro_cliente(restaurante, clinica, enviada):
    """Escopo por cliente: id de outra instancia nao casa nem por acidente."""
    assert aplicar(clinica, status="delivered") == 0

    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.ENVIADA


def test_status_desconhecido_da_meta_e_ignorado(restaurante, enviada):
    assert aplicar(restaurante, status="algo_novo") == 0


def test_repeticao_do_mesmo_status_nao_conta_como_mudanca(restaurante, enviada):
    momento = int((timezone.now() - timedelta(minutes=1)).timestamp())
    aplicar(restaurante, status="delivered", timestamp=momento)

    assert aplicar(restaurante, status="delivered", timestamp=momento) == 0


def test_status_chega_pelo_webhook_e_atualiza_a_mensagem(client, restaurante, enviada, settings):
    """Caminho completo: a Meta posta, a mensagem muda de estado na hora."""
    from tests.payloads_whatsapp import SEGREDO, corpo_e_assinatura

    settings.WHATSAPP_APP_SECRET = SEGREDO
    corpo, assinatura = corpo_e_assinatura(payload_status(status="delivered", categoria="service"))

    resposta = client.post(
        "/webhook/whatsapp/",
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura},
    )

    assert resposta.status_code == 200
    enviada.refresh_from_db()
    assert enviada.status == Mensagem.Status.ENTREGUE
    assert enviada.categoria_cobranca == CategoriaCobranca.SERVICE
