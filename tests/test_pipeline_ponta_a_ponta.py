"""O caminho completo: a Meta entrega, o bot responde, o canal manda.

E o primeiro teste em que o produto faz o que foi vendido. Roda com o canal
em memoria: nada sai para a Meta na suite.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.canal.fake import CanalFake
from apps.catalogo.models import Categoria, Item
from apps.conversas.models import Contato, Conversa, Mensagem
from apps.core import tasks
from apps.core.pipeline import responder_mensagem
from tests.payloads_whatsapp import SEGREDO, corpo_e_assinatura, payload_mensagem

URL = "/webhook/whatsapp/"


@pytest.fixture(autouse=True)
def segredo(settings):
    settings.WHATSAPP_APP_SECRET = SEGREDO


@pytest.fixture
def canal(monkeypatch):
    falso = CanalFake()
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: falso)
    return falso


@pytest.fixture
def menu(restaurante):
    categoria = Categoria.objects.create(tenant=restaurante, nome="Pratos")
    Item.objects.create(
        tenant=restaurante, categoria=categoria, nome="Picanha", preco=Decimal("89.90")
    )
    return categoria


def entregar(client, texto, id_mensagem="wamid.AAA"):
    corpo, assinatura = corpo_e_assinatura(payload_mensagem(texto=texto, id_mensagem=id_mensagem))
    return client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura},
    )


def test_cliente_pergunta_preco_e_recebe_resposta(
    client, restaurante, menu, canal, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        assert entregar(client, "quanto custa a picanha?").status_code == 200

    entrada = Mensagem.objects.get(direcao=Mensagem.Direcao.ENTRADA)
    saida = Mensagem.objects.get(direcao=Mensagem.Direcao.SAIDA)

    assert entrada.respondida_em is not None
    assert "R$ 89,90" in saida.texto
    assert saida.status == Mensagem.Status.ENVIADA
    assert saida.id_externo != ""
    assert canal.enviadas == [{"tipo": "texto", "telefone": "5511999999999", "texto": saida.texto}]


def test_cliente_pede_cardapio_e_recebe_o_cadastrado(
    client, restaurante, menu, canal, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        entregar(client, "manda o cardapio")

    saida = Mensagem.objects.get(direcao=Mensagem.Direcao.SAIDA)

    assert "Pratos" in saida.texto
    assert "Picanha" in saida.texto


def test_reentrega_da_meta_nao_responde_duas_vezes(
    client, restaurante, menu, canal, django_capture_on_commit_callbacks
):
    """Responder em dobro e visivel para o cliente do cliente."""
    for _ in range(2):
        with django_capture_on_commit_callbacks(execute=True):
            entregar(client, "quanto custa a picanha?")

    assert Mensagem.objects.filter(direcao=Mensagem.Direcao.SAIDA).count() == 1
    assert len(canal.enviadas) == 1


def test_tarefa_reentregue_nao_responde_duas_vezes(restaurante, menu, canal):
    """Mesma garantia no nivel da fila, nao so no do webhook."""
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )
    entrada = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="cardapio",
    )

    assert responder_mensagem(entrada.pk) is not None
    assert responder_mensagem(entrada.pk) is None
    assert Mensagem.objects.filter(direcao=Mensagem.Direcao.SAIDA).count() == 1


def test_mensagem_fora_da_janela_nao_e_respondida(restaurante, menu, canal):
    """Se a mensagem ficou mais de 24h na fila, responder custaria template
    para entregar uma resposta velha."""
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(
        tenant=restaurante,
        contato=contato,
        ultima_entrada_em=timezone.now() - timedelta(hours=30),
    )
    entrada = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="cardapio",
    )

    assert responder_mensagem(entrada.pk) is None
    assert not Mensagem.objects.filter(direcao=Mensagem.Direcao.SAIDA).exists()
    assert canal.enviadas == []


def test_mensagem_de_saida_nao_e_respondida(restaurante, menu, canal):
    """Guarda contra o bot conversar com ele mesmo."""
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )
    saida = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        texto="oi",
    )

    assert responder_mensagem(saida.pk) is None
    assert canal.enviadas == []


def test_mensagem_inexistente_nao_explode(restaurante, canal):
    assert responder_mensagem(999999) is None


def test_tier_llm_ainda_cai_no_de_regra(
    client, restaurante, menu, canal, django_capture_on_commit_callbacks
):
    """Enquanto o LLM nao existe, atender por regra e melhor que nao atender."""
    from apps.tenants.models import Configuracao

    configuracao = restaurante.configuracao
    configuracao.responder = Configuracao.Tier.LLM
    configuracao.save(update_fields=["responder", "atualizado_em"])

    with django_capture_on_commit_callbacks(execute=True):
        entregar(client, "quanto custa a picanha?")

    saida = Mensagem.objects.get(direcao=Mensagem.Direcao.SAIDA)
    assert "R$ 89,90" in saida.texto
