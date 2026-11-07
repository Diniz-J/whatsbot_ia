"""Aplicacao do que a Meta reporta sobre as mensagens que enviamos.

Roda na propria request, e nao na fila, de proposito: sao poucos UPDATE por
indice e nada de rede, e enfileirar criaria um ponto onde um status perdido
deixaria a mensagem travada num estado antigo para sempre.
"""

import logging

from django.db import transaction

from apps.canal.base import StatusEntrega
from apps.conversas.models import Mensagem

logger = logging.getLogger(__name__)

# Nome do status na Meta para o nosso.
MAPA = {
    "sent": Mensagem.Status.ENVIADA,
    "delivered": Mensagem.Status.ENTREGUE,
    "read": Mensagem.Status.LIDA,
    "failed": Mensagem.Status.FALHOU,
}

# Progressao normal. Callback chega fora de ordem (`read` antes de
# `delivered` acontece), entao o status so avanca nesta escala. `falhou` fica
# fora dela de proposito: ele nao avanca, encerra — ver `_aplicar`.
ORDEM = {
    Mensagem.Status.ENFILEIRADA: 0,
    Mensagem.Status.ENVIANDO: 1,
    Mensagem.Status.ENVIADA: 2,
    Mensagem.Status.ENTREGUE: 3,
    Mensagem.Status.LIDA: 4,
}

FINAIS_DE_SUCESSO = {Mensagem.Status.ENTREGUE, Mensagem.Status.LIDA}


@transaction.atomic
def aplicar_status(tenant_id: int, status: list[StatusEntrega]) -> int:
    """Atualiza as mensagens que os status referenciam. Devolve quantas mudaram.

    Escopado pelo cliente: um id de outra instancia simplesmente nao casa, e
    nao se procura fora do tenant nem por acidente.
    """
    alteradas = 0
    for reporte in status:
        novo = MAPA.get(reporte.status)
        if novo is None:
            logger.info("Status desconhecido da Meta ignorado: %s", reporte.status)
            continue

        mensagem = (
            Mensagem.objects.select_for_update()
            .filter(tenant_id=tenant_id, id_externo=reporte.id_externo)
            .first()
        )
        if mensagem is None:
            # Status de mensagem que nao e nossa, ou que ainda nao terminou de
            # gravar. Nao e erro: a Meta reenvia o status.
            logger.info("Status para mensagem desconhecida: %s", reporte.id_externo)
            continue

        if _aplicar(mensagem, novo, reporte):
            alteradas += 1
    return alteradas


def _aplicar(mensagem: Mensagem, novo: str, reporte: StatusEntrega) -> bool:
    campos = []

    if novo == Mensagem.Status.FALHOU:
        if mensagem.status in FINAIS_DE_SUCESSO:
            # Contradicao: ja foi entregue ou lida. Mantem o que aconteceu.
            logger.warning(
                "Falha reportada para mensagem %s ja em %s", mensagem.pk, mensagem.status
            )
        elif mensagem.status != novo or mensagem.erro != reporte.erro:
            # Repeticao da mesma falha nao e mudanca: a Meta reenvia status, e
            # regravar o identico inflaria a contagem de alteradas.
            mensagem.status = novo
            mensagem.erro = reporte.erro
            campos += ["status", "erro"]
    elif mensagem.status == Mensagem.Status.FALHOU:
        # `falhou` esta fora da escala de ORDEM de proposito: ele nao avanca,
        # ele encerra. Sem esta guarda um callback fora de ordem (`sent`
        # chegando depois de `failed`) ressuscitaria a mensagem como enviada,
        # com o motivo da falha ainda gravado ao lado — e `enviada` conta como
        # entregue ao cliente no historico da conversa.
        logger.warning(
            "Status %s reportado para mensagem %s que ja falhou; mantido", novo, mensagem.pk
        )
    elif ORDEM.get(novo, -1) > ORDEM.get(mensagem.status, -1):
        mensagem.status = novo
        campos.append("status")

    if reporte.momento is not None and (
        mensagem.status_em is None or reporte.momento > mensagem.status_em
    ):
        mensagem.status_em = reporte.momento
        campos.append("status_em")

    # A categoria vem no primeiro status cobravel e nao muda depois; gravar so
    # quando esta vazia evita que um callback posterior sem preco a apague.
    if reporte.categoria and not mensagem.categoria_cobranca:
        mensagem.categoria_cobranca = reporte.categoria
        campos.append("categoria_cobranca")
    if reporte.cobravel is not None and mensagem.cobravel is None:
        mensagem.cobravel = reporte.cobravel
        campos.append("cobravel")

    if not campos:
        return False
    mensagem.save(update_fields=[*campos, "atualizado_em"])
    return True
