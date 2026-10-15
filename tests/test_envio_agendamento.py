"""A regra que custa dinheiro: texto livre so dentro da janela de 24h."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.conversas.models import Contato, Conversa, Mensagem
from apps.core.envio import JanelaFechada, agendar_template, agendar_texto


@pytest.fixture
def conversa_aberta(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    return Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )


@pytest.fixture
def conversa_fechada(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511988888888")
    return Conversa.objects.create(
        tenant=restaurante,
        contato=contato,
        ultima_entrada_em=timezone.now() - timedelta(hours=25),
    )


def test_texto_dentro_da_janela_e_agendado(conversa_aberta):
    mensagem = agendar_texto(conversa_aberta, "mesa reservada para as 20h")

    assert mensagem.direcao == Mensagem.Direcao.SAIDA
    assert mensagem.status == Mensagem.Status.ENFILEIRADA
    assert mensagem.texto == "mesa reservada para as 20h"
    assert mensagem.id_externo == ""


def test_texto_fora_da_janela_e_recusado(conversa_fechada):
    """Recusa explicita em vez de virar template em silencio: template custa
    dinheiro e a troca tem que ser escolha de quem chamou."""
    with pytest.raises(JanelaFechada):
        agendar_texto(conversa_fechada, "promocao de hoje")

    assert not Mensagem.objects.filter(direcao=Mensagem.Direcao.SAIDA).exists()


def test_conversa_sem_entrada_nunca_aceita_texto(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511977777777")
    conversa = Conversa.objects.create(tenant=restaurante, contato=contato)

    with pytest.raises(JanelaFechada):
        agendar_texto(conversa, "oi")


def test_template_funciona_fora_da_janela(conversa_fechada):
    mensagem = agendar_template(
        conversa_fechada, "confirmacao_reserva", variaveis=["20h", "4 pessoas"]
    )

    assert mensagem.status == Mensagem.Status.ENFILEIRADA
    assert mensagem.template == "confirmacao_reserva"
    assert mensagem.tipo == "template"
    # O corpo aprovado mora na Meta; o historico guarda o que foi mandado.
    assert "confirmacao_reserva" in mensagem.texto
    assert "20h" in mensagem.texto


def test_mensagem_agendada_herda_o_cliente_da_conversa(conversa_aberta):
    mensagem = agendar_texto(conversa_aberta, "ok")

    assert mensagem.tenant_id == conversa_aberta.tenant_id
