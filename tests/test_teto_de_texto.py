"""Resposta comprida nao pode virar silencio.

A Cloud API recusa corpo de texto acima do limite dela, e a recusa chega como
erro permanente: a mensagem fica `falhou` e o cliente nao recebe nada. Antes do
teto de `LLM_MAX_TOKENS` subir para 16000, o proprio limite de token cortava a
resposta e o `stop_reason` derrubava o atendimento no tier de regra — o cliente
recebia algo pior, mas recebia. Com o teto folgado, o modelo entrega texto que
o canal nao aceita.
"""

import pytest
from django.utils import timezone

from apps.conversas.models import Contato, Conversa, Mensagem
from apps.core.envio import MARCA_DE_CORTE, agendar_template, agendar_texto


@pytest.fixture
def conversa(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    return Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )


def test_texto_curto_passa_intacto(conversa, settings):
    settings.WHATSAPP_TEXTO_MAX = 100

    mensagem = agendar_texto(conversa, "Abrimos as 19h.")

    assert mensagem.texto == "Abrimos as 19h."


def test_texto_no_limite_passa_intacto(conversa, settings):
    settings.WHATSAPP_TEXTO_MAX = 50
    exato = "a" * 50

    mensagem = agendar_texto(conversa, exato)

    assert mensagem.texto == exato


def test_texto_acima_do_limite_e_cortado_e_ainda_sai(conversa, settings):
    """O que importa: a mensagem continua enfileirada para envio.

    Sem o corte, a Meta recusaria e o cliente nao receberia nada.
    """
    settings.WHATSAPP_TEXTO_MAX = 50

    mensagem = agendar_texto(conversa, "a" * 500)

    assert len(mensagem.texto) == 50
    assert mensagem.status == Mensagem.Status.ENFILEIRADA


def test_o_corte_fica_visivel_para_quem_le_a_conversa(conversa, settings):
    """A equipe que abre o historico tem que ver que faltou texto."""
    settings.WHATSAPP_TEXTO_MAX = 50

    mensagem = agendar_texto(conversa, "a" * 500)

    assert mensagem.texto.endswith(MARCA_DE_CORTE)


def test_o_banco_guarda_o_que_foi_mandado_e_nao_o_original(conversa, settings):
    """A linha e o registro do que o cliente recebeu; guardar o texto inteiro
    faria o historico mentir sobre o que saiu."""
    settings.WHATSAPP_TEXTO_MAX = 50

    mensagem = agendar_texto(conversa, "a" * 500)
    mensagem.refresh_from_db()

    assert len(mensagem.texto) == 50


def test_o_corte_e_avisado_no_log(conversa, settings, caplog):
    """Chegar aqui quer dizer que o modelo fugiu do prompt, que pede duas ou
    tres frases. O operador precisa ver."""
    settings.WHATSAPP_TEXTO_MAX = 50

    with caplog.at_level("WARNING"):
        agendar_texto(conversa, "a" * 500)

    assert any("cortado" in registro.message for registro in caplog.records)


def test_template_nao_e_cortado_pela_mesma_regra(conversa, settings):
    """O corpo aprovado mora na Meta; o texto guardado e so uma previa."""
    settings.WHATSAPP_TEXTO_MAX = 10

    mensagem = agendar_template(conversa, "lembrete_reserva", variaveis=["sabado as 20h"])

    assert mensagem.texto.startswith("[template lembrete_reserva]")
    assert MARCA_DE_CORTE not in mensagem.texto


def test_limite_menor_que_a_marca_nao_estoura(conversa, settings):
    """Configuracao absurda nao pode levantar erro de slice negativo."""
    settings.WHATSAPP_TEXTO_MAX = 2

    mensagem = agendar_texto(conversa, "a" * 500)

    assert len(mensagem.texto) == 2


def test_teto_de_token_cabe_no_limite_do_canal(settings):
    """`LLM_MAX_TOKENS` tem que ser dimensionado pelo canal, nao pelo modelo.

    Foi o erro do PR que subiu o teto para 16000: eu pensei na folga do
    raciocinio e esqueci que o texto final tem que caber numa mensagem de
    WhatsApp. Com uns 3,5 caracteres por token em portugues, o teto de token
    nao pode projetar texto muito acima do limite do canal.
    """
    projetado = settings.LLM_MAX_TOKENS * 3.5

    assert projetado <= settings.WHATSAPP_TEXTO_MAX * 4, (
        f"LLM_MAX_TOKENS={settings.LLM_MAX_TOKENS} projeta ~{projetado:.0f} caracteres "
        f"contra um canal de {settings.WHATSAPP_TEXTO_MAX}"
    )
