"""Normalizacao de um evento gravado em contato, conversa e mensagem."""

from apps.conversas.models import Contato, EventoWebhook, Mensagem
from apps.core.services import processar_evento
from tests.payloads_whatsapp import payload_mensagem


def test_cria_contato_conversa_e_mensagem(restaurante):
    evento = EventoWebhook.objects.create(
        tenant=restaurante, id_mensagem="wamid.AAA", payload=payload_mensagem()
    )

    (mensagem,) = processar_evento(evento)

    assert mensagem.direcao == Mensagem.Direcao.ENTRADA
    assert mensagem.texto == "oi, qual o horario de voces?"
    assert mensagem.conversa.contato.telefone == "5511999999999"
    assert mensagem.conversa.janela_aberta()


def test_evento_ja_processado_nao_regrava(restaurante):
    """A fila pode reentregar a tarefa depois de ela ter rodado."""
    evento = EventoWebhook.objects.create(
        tenant=restaurante, id_mensagem="wamid.AAA", payload=payload_mensagem()
    )

    assert len(processar_evento(evento)) == 1
    assert processar_evento(evento) == []
    assert Mensagem.objects.count() == 1


def test_nome_do_perfil_e_atualizado(restaurante):
    """A pessoa troca o nome no WhatsApp; a ultima entrega manda."""
    processar_evento(
        EventoWebhook.objects.create(
            tenant=restaurante, id_mensagem="wamid.AAA", payload=payload_mensagem(nome="Ze")
        )
    )

    processar_evento(
        EventoWebhook.objects.create(
            tenant=restaurante,
            id_mensagem="wamid.BBB",
            payload=payload_mensagem(id_mensagem="wamid.BBB", nome="Jose da Silva"),
        )
    )

    assert Contato.objects.count() == 1
    assert Contato.objects.get().nome_perfil == "Jose da Silva"
