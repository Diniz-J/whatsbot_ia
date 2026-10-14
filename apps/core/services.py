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


@transaction.atomic
def processar_evento(evento: EventoWebhook) -> list[Mensagem]:
    """Transforma o payload cru do evento em mensagens gravadas.

    Idempotente de proposito: um evento ja processado volta vazio em vez de
    duplicar mensagem, porque a tarefa pode ser reentregue pela fila mesmo
    depois de ter rodado.
    """
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
        contato, _ = Contato.objects.get_or_create(
            tenant_id=evento.tenant_id,
            telefone=entrada.telefone,
            defaults={"nome_perfil": entrada.nome_perfil},
        )
        # O nome de perfil muda quando a pessoa troca no WhatsApp; a ultima
        # entrega e a mais confiavel.
        if entrada.nome_perfil and contato.nome_perfil != entrada.nome_perfil:
            contato.nome_perfil = entrada.nome_perfil
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

        # A janela de 24h conta da ultima mensagem do cliente, entao ela so
        # anda aqui, no caminho de entrada.
        conversa.ultima_entrada_em = evento.criado_em
        conversa.save(update_fields=["ultima_entrada_em", "atualizado_em"])

    evento.processado_em = timezone.now()
    evento.save(update_fields=["processado_em", "atualizado_em"])
    return criadas
