"""Da mensagem do cliente ate a reserva gravada e a confirmacao enviada.

A confirmacao e a resposta normal do atendimento, dentro da janela de 24h:
texto livre, que a Meta nao cobra. Nao ha template e nao ha lembrete — foi a
decisao desta fatia, e e ela que esta travada aqui.
"""

from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.canal.fake import CanalFake
from apps.catalogo.models import HorarioFuncionamento
from apps.conversas.models import Contato, Conversa, Mensagem
from apps.core import tasks
from apps.core.pipeline import responder_mensagem
from apps.reservas.models import Mesa, Reserva
from apps.responder.llm import cliente as modulo_cliente
from apps.tenants.models import Configuracao
from tests.fake_anthropic import AnthropicFalso, BlocoFerramenta, BlocoTexto, RespostaFalsa

FUSO = ZoneInfo("America/Sao_Paulo")


def proximo_sabado() -> date:
    hoje = timezone.localdate()
    return hoje + timedelta(days=(5 - hoje.weekday()) % 7 + 7)


SABADO = proximo_sabado()


@pytest.fixture(autouse=True)
def fuso_do_negocio(settings):
    settings.TIME_ZONE = "America/Sao_Paulo"
    settings.ANTHROPIC_API_KEY = "chave-de-teste"
    timezone.activate(FUSO)
    yield
    timezone.deactivate()


@pytest.fixture
def casa(restaurante):
    configuracao = restaurante.configuracao
    configuracao.responder = Configuracao.Tier.LLM
    configuracao.save(update_fields=["responder", "atualizado_em"])
    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=SABADO.weekday(), abre=time(19, 0), fecha=time(23, 0)
    )
    Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)
    return restaurante


@pytest.fixture
def conversa(casa):
    contato = Contato.objects.create(tenant=casa, telefone="5511999999999", nome_perfil="Ana")
    return Conversa.objects.create(tenant=casa, contato=contato, ultima_entrada_em=timezone.now())


def modelo_que_fecha(monkeypatch, hora="20:00", pessoas=4):
    """Roteiro de duas voltas: pede a ferramenta, depois redige."""
    construir = AnthropicFalso.com(
        [
            RespostaFalsa(
                [
                    BlocoFerramenta(
                        "fechar_reserva",
                        {"data_hora": f"{SABADO}T{hora}", "pessoas": pessoas},
                    )
                ],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto(f"Reservado para {pessoas} as {hora}. Ate sabado!")]),
        ]
    )
    monkeypatch.setattr(modulo_cliente.anthropic, "Anthropic", construir)
    return construir


def pedir(conversa, texto="quero reservar sabado as 20h para quatro"):
    return Mensagem.objects.create(
        tenant=conversa.tenant,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto=texto,
    )


def test_reserva_gravada_e_confirmacao_como_texto_livre(conversa, monkeypatch):
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: CanalFake())
    modelo_que_fecha(monkeypatch)
    entrada = pedir(conversa)

    saida = responder_mensagem(entrada.pk)

    reserva = Reserva.objects.get()
    assert reserva.pessoas == 4
    assert reserva.status == Reserva.Status.CONFIRMADA
    assert reserva.contato == conversa.contato
    # Confirmacao e a resposta normal: texto, nao template.
    assert saida.tipo == "text"
    assert saida.template == ""
    assert "Reservado" in saida.texto


def test_o_modelo_ve_o_resultado_da_ferramenta_antes_de_redigir(conversa, monkeypatch):
    """A confirmacao sai depois do CONFIRMADA, nunca antes."""
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: CanalFake())
    construir = modelo_que_fecha(monkeypatch)
    entrada = pedir(conversa)

    responder_mensagem(entrada.pk)

    segunda_volta = construir.mensagens.chamadas[1]["messages"]
    resultado = segunda_volta[-1]["content"][0]["content"]
    assert "RESERVA CONFIRMADA" in resultado


def test_recusa_da_ferramenta_nao_grava_e_o_cliente_e_avisado(conversa, monkeypatch):
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: CanalFake())
    construir = AnthropicFalso.com(
        [
            RespostaFalsa(
                [
                    BlocoFerramenta(
                        "fechar_reserva",
                        {"data_hora": f"{SABADO}T15:00", "pessoas": 4},
                    )
                ],
                stop_reason="tool_use",
            ),
            RespostaFalsa([BlocoTexto("As 15h nao atendemos. Abrimos as 19h.")]),
        ]
    )
    monkeypatch.setattr(modulo_cliente.anthropic, "Anthropic", construir)
    entrada = pedir(conversa, "reserva sabado as 15h")

    saida = responder_mensagem(entrada.pk)

    assert not Reserva.objects.exists()
    assert "19h" in saida.texto
    resultado = construir.mensagens.chamadas[1]["messages"][-1]["content"][0]["content"]
    assert "NAO FECHADA" in resultado


def test_fora_da_janela_a_reserva_fica_e_a_confirmacao_nao_sai(conversa, monkeypatch):
    """Caso deliberado, nao defeito, e por isso esta travado aqui.

    O `Responder` nao conhece a janela de proposito (e politica de canal), e a
    ferramenta roda antes de o envio ser tentado. Entao uma entrada que ficou
    mais de 24h na fila grava a reserva e perde a confirmacao. O cliente pediu
    a reserva; a equipe ve no back-office e tem o telefone. Mandar por template
    seria gastar dinheiro para entregar uma resposta velha.
    """
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: CanalFake())
    modelo_que_fecha(monkeypatch)
    Conversa.objects.filter(pk=conversa.pk).update(
        ultima_entrada_em=timezone.now() - timedelta(hours=25)
    )
    entrada = pedir(conversa)

    saida = responder_mensagem(entrada.pk)

    assert saida is None
    assert Reserva.objects.count() == 1
    assert not Mensagem.objects.filter(direcao=Mensagem.Direcao.SAIDA).exists()


def test_reprocessar_a_mesma_entrada_nao_reserva_duas_vezes(conversa, monkeypatch):
    """A fila e no minimo-uma-vez; o lock do pipeline e o que segura."""
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: CanalFake())
    modelo_que_fecha(monkeypatch)
    entrada = pedir(conversa)

    responder_mensagem(entrada.pk)
    segunda = responder_mensagem(entrada.pk)

    assert segunda is None
    assert Reserva.objects.count() == 1
