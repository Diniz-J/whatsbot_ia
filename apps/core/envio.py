"""Agendamento de mensagem de saida.

A regra que mora aqui e uma so, e e a que custa dinheiro: dentro da janela de
24h cabe texto livre, fora dela so template aprovado. O canal nao decide isso
e o `Responder` nao precisa saber — ele pede, e aqui recusa ou agenda.
"""

import logging

from django.conf import settings
from django.db import transaction

from apps.conversas.models import Conversa, Mensagem

logger = logging.getLogger(__name__)

# Marca que fica no fim do texto cortado, para quem le a conversa no admin ver
# que faltou pedaco em vez de achar que o bot parou no meio da frase.
MARCA_DE_CORTE = "..."


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
    return _agendar(conversa, texto=_caber_no_canal(texto), tipo="text", uso=uso)


def _caber_no_canal(texto: str) -> str:
    """Corta o texto no tamanho que a Cloud API aceita.

    Acima do limite a Meta recusa, e a recusa chega como erro permanente: a
    mensagem fica `falhou` e o cliente nao recebe nada. Frase cortada e pior
    que frase inteira, e muito melhor que silencio.

    Ha tensao consciente com a decisao de recusar resposta truncada pelo
    modelo (`stop_reason` de max_tokens): la o que nao da para confiar e um
    pedido de ferramenta cortado no meio, e existe o tier de regra para
    assumir a resposta. Aqui o texto final ja esta pronto e a alternativa e
    nao entregar nada.

    E guarda de ultimo recurso. `LLM_MAX_TOKENS` e dimensionado para a
    resposta caber no canal, entao chegar neste corte quer dizer que o modelo
    fugiu do prompt, que pede duas ou tres frases — por isso o aviso no log.
    """
    limite = settings.WHATSAPP_TEXTO_MAX
    if len(texto) <= limite:
        return texto

    logger.warning("Texto de %s caracteres cortado em %s para caber no canal", len(texto), limite)
    if limite <= len(MARCA_DE_CORTE):
        return texto[:limite]
    return texto[: limite - len(MARCA_DE_CORTE)] + MARCA_DE_CORTE


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
