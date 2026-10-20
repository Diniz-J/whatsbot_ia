"""Varredura do que ficou preso num estado nao-final.

Sem ela, quatro coisas somem em silencio: mensagem reservada cujo worker
morreu (ninguem mais a reclama, porque o claim impede), saida gravada que
nunca foi reclamada, entrega gravada cujo enfileiramento se perdeu e entrada
que nunca foi atendida.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.canal.fake import CanalFake
from apps.conversas.models import JANELA_SERVICO, Contato, Conversa, EventoWebhook, Mensagem
from apps.core import pipeline, tasks
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


def saida_parada(restaurante, conversa, template="", idade_em_minutos=0):
    """Mensagem gravada que nunca foi reclamada pela tarefa de envio."""
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        status=Mensagem.Status.ENFILEIRADA,
        texto="oi",
        template=template,
    )
    if idade_em_minutos:
        Mensagem.objects.filter(pk=mensagem.pk).update(
            atualizado_em=timezone.now() - timedelta(minutes=idade_em_minutos),
            criado_em=timezone.now() - timedelta(minutes=idade_em_minutos),
        )
    return mensagem


def test_saida_parada_na_fila_volta_a_ser_enviada(restaurante, conversa, canal, settings):
    """O `enviando` tem quem o reclame; o `enfileirada` nao tinha.

    Acontece quando o enfileiramento nao chegou ao broker, quando o worker
    morreu antes de reservar, ou quando as retentativas se esgotaram — e nos
    tres a resposta nunca sai.
    """
    mensagem = saida_parada(
        restaurante, conversa, idade_em_minutos=settings.ENVIO_TRAVADO_MINUTOS + 1
    )

    assert tasks.varrer_pendentes()["saidas_paradas"] == 1

    mensagem.refresh_from_db()
    assert mensagem.status == Mensagem.Status.ENVIADA
    assert len(canal.enviadas) == 1


def test_saida_recem_enfileirada_espera(restaurante, conversa, canal):
    """A tarefa recem-enfileirada ainda vai rodar; reclamar agora duplicaria."""
    saida_parada(restaurante, conversa)

    assert tasks.varrer_pendentes()["saidas_paradas"] == 0
    assert canal.enviadas == []


def test_saida_fora_da_janela_nao_e_mais_reenfileirada(restaurante, conversa, canal):
    """Teto de idade, como no reatendimento: fora da janela de 24h a Meta
    recusa texto livre, entao insistir nao entrega nada."""
    saida_parada(
        restaurante,
        conversa,
        idade_em_minutos=int(JANELA_SERVICO.total_seconds() // 60) + 1,
    )

    assert tasks.varrer_pendentes()["saidas_paradas"] == 0
    assert canal.enviadas == []


def test_template_parado_nao_e_reenfileirado_as_cegas(restaurante, conversa, canal, settings):
    """Idioma e variaveis vao como argumento da tarefa, nao na linha.

    Reenfileirar pelo id mandaria o template sem os dados que ele preenche.
    Enquanto nao estiverem gravados, aparecer como pendente e melhor.
    """
    saida_parada(
        restaurante,
        conversa,
        template="confirmacao_reserva",
        idade_em_minutos=settings.ENVIO_TRAVADO_MINUTOS + 1,
    )

    assert tasks.varrer_pendentes()["saidas_paradas"] == 0
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
    assert tasks.varrer_pendentes() == {
        "envios": 0,
        "saidas_paradas": 0,
        "entregas": 0,
        "respostas": 0,
    }


def test_entrada_fora_da_janela_nao_e_mais_reatendida(restaurante, conversa, canal):
    """Passada a janela de 24h o pipeline ja nao consegue responder.

    Sem teto de idade a varredura insiste para sempre numa entrada que o
    atendimento nunca conclui: o `transaction.atomic` do pipeline desfaz o
    carimbo de `respondida_em` quando o atendimento estoura excecao, a
    mensagem volta a ser nula e nada mais a tira da fila. Como `agendar_texto`
    recusaria o envio com `JanelaFechada`, insistir e so gastar.
    """
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="qual o horario?",
    )
    Mensagem.objects.filter(pk=mensagem.pk).update(
        criado_em=timezone.now() - JANELA_SERVICO - timedelta(minutes=1)
    )

    assert tasks.varrer_pendentes()["respostas"] == 0


def test_entrada_dentro_da_janela_continua_sendo_reatendida(restaurante, conversa, canal, settings):
    """O teto nao pode engolir o caso que a varredura existe para resolver."""
    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="qual o horario?",
    )
    Mensagem.objects.filter(pk=mensagem.pk).update(
        criado_em=timezone.now() - JANELA_SERVICO + timedelta(hours=1)
    )

    assert tasks.varrer_pendentes()["respostas"] == 1


def test_atendimento_que_estoura_nao_vira_reatendimento_eterno(
    restaurante, conversa, canal, monkeypatch
):
    """Reproduz o loop: excecao no atendimento desfaz o carimbo no rollback.

    Antes do teto, estas voltas se repetiriam a cada varredura indefinidamente,
    e no tier de IA cada uma pode gastar token antes de estourar.
    """

    class Explode:
        def handle(self, pergunta, contexto):
            raise TypeError("defeito nao previsto no atendimento")

    monkeypatch.setattr(pipeline, "responder_para", lambda configuracao: Explode())
    # A varredura so enfileira; em teste o Celery roda na hora, e o que
    # interessa aqui e o que ela escolhe, nao quem executa.
    enfileiradas = []
    monkeypatch.setattr(tasks.responder_inbound, "delay", enfileiradas.append)

    mensagem = Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="texto que derruba o atendimento",
    )
    Mensagem.objects.filter(pk=mensagem.pk).update(criado_em=timezone.now() - timedelta(hours=1))

    # Dentro da janela a varredura insiste, e o rollback mantem o campo nulo.
    assert tasks.varrer_pendentes()["respostas"] == 1
    assert enfileiradas == [mensagem.pk]
    with pytest.raises(TypeError):
        pipeline.responder_mensagem(mensagem.pk)
    mensagem.refresh_from_db()
    assert mensagem.respondida_em is None

    # Passada a janela, para de insistir.
    Mensagem.objects.filter(pk=mensagem.pk).update(
        criado_em=timezone.now() - JANELA_SERVICO - timedelta(minutes=1)
    )

    assert tasks.varrer_pendentes()["respostas"] == 0
