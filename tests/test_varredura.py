"""Varredura do que ficou preso num estado nao-final.

Sem ela, duas coisas somem em silencio: mensagem reservada cujo worker morreu
(ninguem mais a reclama, porque o claim impede), e entrega gravada cujo
enfileiramento se perdeu.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.canal.fake import CanalFake
from apps.conversas.models import Contato, Conversa, EventoWebhook, Mensagem
from apps.core import tasks
from tests.payloads_whatsapp import payload_mensagem


@pytest.fixture
def canal(monkeypatch):
    falso = CanalFake()
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: falso)
    return falso


@pytest.fixture
def conversa(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    return Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )


def envenelhecer_mensagem(mensagem, minutos):
    Mensagem.objects.filter(pk=mensagem.pk).update(
        atualizado_em=timezone.now() - timedelta(minutes=minutos)
    )


def test_envio_travado_volta_para_a_fila_e_sai(restaurante, conversa, canal, settings):
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        status=Mensagem.Status.ENVIANDO,
        texto="oi",
    )
    envenelhecer_mensagem(mensagem, settings.ENVIO_TRAVADO_MINUTOS + 1)

    assert tasks.varrer_pendentes()["envios"] == 1

    mensagem.refresh_from_db()
    assert mensagem.status == Mensagem.Status.ENVIADA
    assert len(canal.enviadas) == 1


def test_envio_recem_reservado_nao_e_mexido(restaurante, conversa, canal):
    """Timeout de envio e de segundos, mas reclamar cedo duplicaria mensagem
    que ainda esta em voo."""
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        status=Mensagem.Status.ENVIANDO,
        texto="oi",
    )

    assert tasks.varrer_pendentes()["envios"] == 0

    mensagem.refresh_from_db()
    assert mensagem.status == Mensagem.Status.ENVIANDO
    assert canal.enviadas == []


def test_mensagem_ja_enviada_nao_e_varrida(restaurante, conversa, canal, settings):
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        status=Mensagem.Status.ENVIADA,
        id_externo="wamid.JA",
        texto="oi",
    )
    envenelhecer_mensagem(mensagem, settings.ENVIO_TRAVADO_MINUTOS + 10)

    assert tasks.varrer_pendentes()["envios"] == 0
    assert canal.enviadas == []


def test_entrega_nao_processada_e_reprocessada(restaurante, canal, settings):
    evento = EventoWebhook.objects.create(
        tenant=restaurante, id_mensagem="wamid.AAA", payload=payload_mensagem()
    )
    EventoWebhook.objects.filter(pk=evento.pk).update(
        criado_em=timezone.now() - timedelta(minutes=settings.INBOUND_TRAVADO_MINUTOS + 1)
    )

    assert tasks.varrer_pendentes()["entregas"] == 1

    evento.refresh_from_db()
    assert evento.processado_em is not None
    assert Mensagem.objects.filter(direcao=Mensagem.Direcao.ENTRADA).count() == 1


def test_entrega_recente_nao_processada_espera(restaurante, canal):
    """A tarefa normal pode so estar na fila ainda."""
    EventoWebhook.objects.create(
        tenant=restaurante, id_mensagem="wamid.AAA", payload=payload_mensagem()
    )

    assert tasks.varrer_pendentes()["entregas"] == 0


def test_entrega_ja_processada_nao_e_varrida(restaurante, canal, settings):
    evento = EventoWebhook.objects.create(
        tenant=restaurante,
        id_mensagem="wamid.AAA",
        payload=payload_mensagem(),
        processado_em=timezone.now(),
    )
    EventoWebhook.objects.filter(pk=evento.pk).update(
        criado_em=timezone.now() - timedelta(minutes=settings.INBOUND_TRAVADO_MINUTOS + 10)
    )

    assert tasks.varrer_pendentes()["entregas"] == 0


def test_entrada_sem_resposta_e_reatendida(restaurante, conversa, canal, settings):
    """Entrada gravada cujo `responder_inbound` se perdeu nao fica orfa: o
    evento ja consta processado, entao so esta varredura a alcanca."""
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="qual o horario?",
    )
    Mensagem.objects.filter(pk=mensagem.pk).update(
        criado_em=timezone.now() - timedelta(minutes=settings.RESPOSTA_TRAVADA_MINUTOS + 1)
    )

    assert tasks.varrer_pendentes()["respostas"] == 1

    mensagem.refresh_from_db()
    assert mensagem.respondida_em is not None


def test_entrada_recente_sem_resposta_espera(restaurante, conversa, canal):
    """A tarefa de atendimento normal pode so estar na fila ainda."""
    Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="oi",
    )

    assert tasks.varrer_pendentes()["respostas"] == 0


def test_entrada_ja_respondida_nao_e_reatendida(restaurante, conversa, canal, settings):
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="oi",
        respondida_em=timezone.now(),
    )
    Mensagem.objects.filter(pk=mensagem.pk).update(
        criado_em=timezone.now() - timedelta(minutes=settings.RESPOSTA_TRAVADA_MINUTOS + 10)
    )

    assert tasks.varrer_pendentes()["respostas"] == 0


def test_varredura_sem_nada_pendente_nao_faz_nada(restaurante, canal):
    assert tasks.varrer_pendentes() == {"envios": 0, "entregas": 0, "respostas": 0}
