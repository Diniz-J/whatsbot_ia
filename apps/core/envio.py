"""Agendamento de mensagem de saida.

A regra que mora aqui e uma so, e e a que custa dinheiro: dentro da janela de
24h cabe texto livre, fora dela so template aprovado. O canal nao decide isso
e o `Responder` nao precisa saber — ele pede, e aqui recusa ou agenda.
"""

import logging

from django.db import transaction

from apps.conversas.models import Conversa, Mensagem

logger = logging.getLogger(__name__)


class JanelaFechada(Exception):
    """Texto livre pedido fora da janela de 24h.

    Erro explicito em vez de virar template em silencio: template custa
    dinheiro e a categoria e decidida pela Meta, entao a troca tem que ser
    escolha de quem chamou.
    """


def agendar_texto(conversa: Conversa, texto: str, uso=None) -> Mensagem:
    """`uso` e o custo em token de quem redigiu, quando houve custo.

    O tier de regra passa `None`: nao gasta token, e gravar zero confundiria
    com gasto medido.
    """
    if not conversa.janela_aberta():
        raise JanelaFechada(f"conversa {conversa.pk} esta fora da janela de 24h: use template")
    return _agendar(conversa, texto=texto, tipo="text", uso=uso)


def agendar_template(
    conversa: Conversa, template: str, idioma: str = "pt_BR", variaveis: list[str] | None = None
) -> Mensagem:
    """Template vale dentro e fora da janela; e o unico caminho fora dela."""
    return _agendar(
        conversa,
        texto=_previa(template, variaveis),
        tipo="template",
        template=template,
        idioma=idioma,
        variaveis=variaveis or [],
    )


def _agendar(conversa: Conversa, texto: str, tipo: str, **extra) -> Mensagem:
    """Grava a mensagem antes de enfileirar.

    A linha existir antes da chamada de rede e o que faz uma queda no meio do
    envio deixar rastro: ela fica `enfileirada` e aparece como pendente, em
    vez de nao ter existido.
    """
    from apps.core.tasks import enviar_outbound

    uso = extra.get("uso")
    mensagem = Mensagem.objects.create(
        tenant_id=conversa.tenant_id,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        status=Mensagem.Status.ENFILEIRADA,
        tipo=tipo,
        texto=texto,
        template=extra.get("template", ""),
        # Guardado na propria mensagem, igual a categoria de cobranca: o
        # faturamento soma as duas fontes de custo numa consulta.
        tokens_entrada=getattr(uso, "entrada", None),
        tokens_saida=getattr(uso, "saida", None),
    )
    transaction.on_commit(
        lambda: enviar_outbound.delay(
            mensagem.pk,
            idioma=extra.get("idioma", ""),
            variaveis=extra.get("variaveis", []),
        )
    )
    return mensagem


def _previa(template: str, variaveis: list[str] | None) -> str:
    """Texto guardado para a mensagem de template aparecer no historico.

    O corpo aprovado mora na Meta, nao aqui; guardamos nome e variaveis para
    quem ler a conversa no admin entender o que foi mandado.
    """
    if variaveis:
        return f"[template {template}] {' | '.join(variaveis)}"
    return f"[template {template}]"
