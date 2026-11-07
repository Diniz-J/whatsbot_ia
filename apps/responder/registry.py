"""Escolha do atendimento conforme o tier do cliente.

O upsell de `rules` para `llm` e esta funcao lendo um campo diferente — sem
deploy, sem migracao, sem fork.

E tambem onde o teto de gasto age, porque passar do teto e exatamente uma
decisao de tier: o cliente volta para o atendimento que nao custa token. Posto
aqui, o modulo de IA segue sem saber que existe orcamento.
"""

import logging

from django.conf import settings

from apps.metricas import services as metricas
from apps.responder.base import Responder
from apps.responder.rules import RuleResponder
from apps.tenants.models import Configuracao

logger = logging.getLogger(__name__)


def responder_para(configuracao: Configuracao) -> Responder:
    if configuracao.responder == Configuracao.Tier.LLM and not _estourou_o_teto(
        configuracao.tenant_id
    ):
        # Import local: o tier de regra nao precisa do SDK da Anthropic
        # carregado para atender.
        from apps.responder.llm.responder import LLMResponder

        return LLMResponder(nome_negocio=configuracao.tenant.nome)
    return RuleResponder()


def _estourou_o_teto(tenant_id: int) -> bool:
    """Se o cliente ja gastou o teto de token deste mes.

    Cair na regra em vez de parar de responder: o cliente final nao tem culpa
    do orcamento, e o dono do negocio paga por um atendimento que funciona.
    Atender pior e melhor que nao atender — a mesma escolha do fallback de
    falha do modelo.

    O teto e conferido antes da chamada, entao o atendimento que o cruza
    termina e passa um pouco. Contar antes custaria uma chamada para saber
    quanto a chamada vai custar.
    """
    teto = settings.LLM_TETO_TOKENS_MES
    if teto <= 0:
        return False

    gasto = metricas.tokens_do_mes_corrente(tenant_id)
    if gasto < teto:
        return False

    logger.warning(
        "Cliente %s passou o teto de %s token no mes (%s): atendendo pela regra",
        tenant_id,
        teto,
        gasto,
    )
    return True
