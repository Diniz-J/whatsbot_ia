"""Normalizacao de uma entrega crua em conversa e mensagem.

Mora em `core` porque e orquestracao: junta o que o canal traduziu com o que
`conversas` guarda. Nao conhece HTTP nem o formato da Meta.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.canal.whatsapp.canal import CanalWhatsApp
from apps.conversas.models import Contato, Conversa, EventoWebhook, Mensagem

logger = logging.getLogger(__name__)

# Tamanho da coluna, lido dela: o limite e do banco, e repetir o numero aqui
# faria as duas verdades divergirem no dia em que a coluna crescer.
LIMITE_NOME_PERFIL = Contato._meta.get_field("nome_perfil").max_length


@transaction.atomic
def processar_evento(evento: EventoWebhook) -> list[Mensagem]:
    """Transforma o payload cru do evento em mensagens gravadas.

    Idempotente de proposito: um evento ja processado volta vazio em vez de
    duplicar mensagem, porque a tarefa pode ser reentregue pela fila mesmo
    depois de ter rodado — e pode ser reentregue enquanto a primeira ainda
    roda, por isso a linha e lida com lock e nao so consultada. Sem o lock,
    as duas execucoes leriam `processado_em` nulo e as duas gravariam.
    """
    evento = EventoWebhook.objects.select_for_update().get(pk=evento.pk)
    if evento.processado_em is not None:
        logger.info("Evento %s ja processado, ignorando", evento.id_mensagem)
        return []

    criadas = []
    for entrada in CanalWhatsApp().normalizar_evento(evento.payload):
        # Uma entrega pode trazer varias mensagens, e cada uma gerou o seu
        # proprio evento com o payload inteiro. Sem este filtro, processar o
        # evento da primeira criaria tambem a mensagem da segunda, e o evento
        # da segunda a criaria de novo.
        if entrada.id_externo != evento.id_mensagem:
            continue

        # Cortado no tamanho da coluna: o nome e texto que a pessoa escreve no
        # perfil dela, e um maior que a coluna viraria DataError aqui dentro —
        # com o evento nunca marcado como processado e a varredura retentando
        # para sempre. Quem grava responde pelo limite do banco.
        nome_perfil = entrada.nome_perfil[:LIMITE_NOME_PERFIL]

        contato, _ = Contato.objects.get_or_create(
            tenant_id=evento.tenant_id,
            telefone=entrada.telefone,
            defaults={"nome_perfil": nome_perfil},
        )
        # O nome de perfil muda quando a pessoa troca no WhatsApp; a ultima
        # entrega e a mais confiavel.
        if nome_perfil and contato.nome_perfil != nome_perfil:
            contato.nome_perfil = nome_perfil
            contato.save(update_fields=["nome_perfil", "atualizado_em"])

        conversa, _ = Conversa.objects.get_or_create(tenant_id=evento.tenant_id, contato=contato)

        criadas.append(
            Mensagem.objects.create(
                tenant_id=evento.tenant_id,
                conversa=conversa,
                direcao=Mensagem.Direcao.ENTRADA,
                id_externo=entrada.id_externo,
                tipo=entrada.tipo,
                texto=entrada.texto,
            )
        )

        _avancar_janela(conversa, entrada.enviada_em or evento.criado_em)

    evento.processado_em = timezone.now()
    evento.save(update_fields=["processado_em", "atualizado_em"])
    return criadas


def _avancar_janela(conversa: Conversa, momento) -> None:
    """Move a janela de 24h para frente, nunca para tras.

    A janela conta da ultima mensagem do cliente, entao ela so anda no
    caminho de entrada — e so para frente: processar fora de ordem, ou
    reprocessar uma entrega antiga, nao pode encurtar uma janela que ja
    estava aberta por uma mensagem mais nova.
    """
    if conversa.ultima_entrada_em is not None and momento <= conversa.ultima_entrada_em:
        return
    conversa.ultima_entrada_em = momento
    conversa.save(update_fields=["ultima_entrada_em", "atualizado_em"])
