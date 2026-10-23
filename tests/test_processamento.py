"""Normalizacao de um evento gravado em contato, conversa e mensagem."""

from apps.conversas.models import Contato, EventoWebhook, Mensagem
from apps.core.services import LIMITE_NOME_PERFIL, processar_evento
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


def test_nome_do_perfil_longo_e_cortado_no_tamanho_da_coluna(restaurante):
    """O nome e texto que a pessoa escreve: nao da para confiar no tamanho.

    Sem o corte, um nome maior que a coluna viraria DataError no meio do
    processamento, com o evento nunca marcado como processado e a varredura
    retentando a mesma entrega para sempre.
    """
    evento = EventoWebhook.objects.create(
        tenant=restaurante,
        id_mensagem="wamid.AAA",
        payload=payload_mensagem(nome="a" * 500),
    )

    processar_evento(evento)

    assert len(Contato.objects.get().nome_perfil) == LIMITE_NOME_PERFIL


def test_nome_do_perfil_longo_nao_atualiza_a_cada_entrega(restaurante):
    """A comparacao tem que ser contra o nome ja cortado.

    Contra o nome cru, o gravado nunca seria igual ao recebido e toda entrega
    daquele contato faria um UPDATE a mais, de graca.
    """

    def processar(identificador):
        processar_evento(
            EventoWebhook.objects.create(
                tenant=restaurante,
                id_mensagem=identificador,
                payload=payload_mensagem(id_mensagem=identificador, nome="a" * 500),
            )
        )

    processar("wamid.AAA")
    primeiro_update = Contato.objects.get().atualizado_em

    processar("wamid.BBB")

    assert Contato.objects.get().atualizado_em == primeiro_update
