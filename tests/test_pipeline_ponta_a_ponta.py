"""O caminho completo: a Meta entrega, o bot responde, o canal manda.

E o primeiro teste em que o produto faz o que foi vendido. Roda com o canal
em memoria: nada sai para a Meta na suite.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.utils import timezone

from apps.canal.fake import CanalFake
from apps.catalogo.models import Categoria, Item
from apps.conversas.models import Contato, Conversa, Mensagem
from apps.core import pipeline, tasks
from apps.core.pipeline import responder_mensagem
from apps.responder.base import Resposta
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


@pytest.fixture
def entrada_aberta(restaurante):
    """Entrada por responder, com a janela de 24h aberta."""
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )
    return Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="cardapio",
    )


@pytest.mark.django_db(transaction=True)
def test_atendimento_roda_fora_de_transacao(restaurante, menu, canal, monkeypatch):
    """Redigir pode levar dezenas de segundos falando com a Anthropic.

    Chamada de rede nao pode segurar lock de linha nem transacao aberta — e a
    mesma regra que o `enviar_outbound` ja segue no envio. Sem `transaction=True`
    o proprio teste rodaria dentro de uma transacao e a afirmacao nao valeria
    nada.
    """
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
    dentro_de_transacao = []

    class Espia:
        def handle(self, pergunta, contexto):
            dentro_de_transacao.append(transaction.get_connection().in_atomic_block)
            return Resposta("o cardapio esta no site")

    monkeypatch.setattr(pipeline, "responder_para", lambda configuracao: Espia())

    assert responder_mensagem(entrada.pk) is not None
    assert dentro_de_transacao == [False]


def test_quem_perde_a_corrida_descarta_a_resposta(
    restaurante, menu, canal, entrada_aberta, monkeypatch
):
    """Duas execucoes podem redigir ao mesmo tempo; so uma responde.

    O carimbo nao e mais lido antes de redigir, entao a unicidade e da fase de
    gravacao. Aqui a segunda execucao comeca a redigir e, no meio, a primeira
    conclui: a segunda tem que descartar o proprio texto em vez de mandar.
    Resposta dobrada o cliente ve, e num template seria cobrada.
    """

    class RespondeDepoisDeOutro:
        """Simula a corrida: conclui a primeira execucao durante a redacao."""

        def __init__(self):
            self.redigiu = 0

        def handle(self, pergunta, contexto):
            self.redigiu += 1
            if self.redigiu == 1:
                Mensagem.objects.filter(pk=entrada_aberta.pk).update(respondida_em=timezone.now())
            return Resposta("o cardapio esta no site")

    espiao = RespondeDepoisDeOutro()
    monkeypatch.setattr(pipeline, "responder_para", lambda configuracao: espiao)

    assert responder_mensagem(entrada_aberta.pk) is None
    assert espiao.redigiu == 1
    assert not Mensagem.objects.filter(direcao=Mensagem.Direcao.SAIDA).exists()
    assert canal.enviadas == []


def test_excecao_ao_redigir_deixa_a_entrada_para_a_varredura(
    restaurante, menu, canal, entrada_aberta, monkeypatch
):
    """A propriedade que o teto de reatendimento assume continua valendo.

    Antes vinha do rollback da transacao unica; agora vem de o carimbo so
    acontecer depois de redigir. O efeito para a varredura e o mesmo.
    """

    class Explode:
        def handle(self, pergunta, contexto):
            raise TypeError("defeito nao previsto no atendimento")

    monkeypatch.setattr(pipeline, "responder_para", lambda configuracao: Explode())

    with pytest.raises(TypeError):
        responder_mensagem(entrada_aberta.pk)

    entrada_aberta.refresh_from_db()
    assert entrada_aberta.respondida_em is None
    assert not Mensagem.objects.filter(direcao=Mensagem.Direcao.SAIDA).exists()
