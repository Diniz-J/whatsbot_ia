"""O custo em token de uma resposta fica na mensagem que ela gerou.

Sem isso nao ha como faturar nem medir margem: o gasto existiria so no log.
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.canal.fake import CanalFake
from apps.catalogo.models import Categoria, Item
from apps.conversas.models import Mensagem
from apps.core import tasks
from apps.responder.llm import cliente as modulo_cliente
from apps.tenants.models import Configuracao
from tests.fake_anthropic import AnthropicFalso, BlocoFerramenta, BlocoTexto, RespostaFalsa
from tests.payloads_whatsapp import SEGREDO, corpo_e_assinatura, payload_mensagem


@pytest.fixture(autouse=True)
def chave(settings):
    settings.ANTHROPIC_API_KEY = "chave-de-teste"


@pytest.fixture
def menu(restaurante):
    categoria = Categoria.objects.create(tenant=restaurante, nome="Pratos")
    Item.objects.create(
        tenant=restaurante, categoria=categoria, nome="Picanha", preco=Decimal("89.90")
    )
    return categoria


def instalar(monkeypatch, roteiro=(), erro=None):
    construir = AnthropicFalso.com(roteiro, erro)
    monkeypatch.setattr(modulo_cliente.anthropic, "Anthropic", construir)
    return construir.mensagens


def test_custo_em_tokens_fica_gravado_na_mensagem_enviada(
    client, monkeypatch, restaurante, menu, django_capture_on_commit_callbacks, settings
):
    """Ponta a ponta no tier llm: o token gasto tem que sobrar no banco, ou
    nao ha como faturar nem medir margem."""
    settings.WHATSAPP_APP_SECRET = SEGREDO
    configuracao = restaurante.configuracao
    configuracao.responder = Configuracao.Tier.LLM
    configuracao.save(update_fields=["responder", "atualizado_em"])
    monkeypatch.setattr(tasks, "canal_para", lambda c: CanalFake())
    instalar(
        monkeypatch,
        [
            RespostaFalsa(
                [BlocoFerramenta("buscar_item", {"termo": "picanha"})],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto("A picanha sai por R$ 89,90.")]),
        ],
    )
    corpo, assinatura = corpo_e_assinatura(payload_mensagem(texto="quanto a picanha?"))

    with django_capture_on_commit_callbacks(execute=True):
        resposta = client.post(
            "/webhook/whatsapp/",
            data=corpo,
            content_type="application/json",
            headers={"X-Hub-Signature-256": assinatura},
        )

    assert resposta.status_code == 200
    saida = Mensagem.objects.get(direcao=Mensagem.Direcao.SAIDA)
    assert "R$ 89,90" in saida.texto
    assert saida.tokens_entrada == 200
    assert saida.tokens_saida == 40


def test_resposta_da_regra_nao_grava_token(restaurante, menu):
    """Tier de regra nao custa token; gravar zero confundiria com gasto real."""
    from apps.conversas.models import Contato, Conversa
    from apps.core.pipeline import responder_mensagem

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

    saida = responder_mensagem(entrada.pk)

    assert saida.tokens_entrada is None
    assert saida.tokens_saida is None
