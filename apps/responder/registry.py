"""Escolha do atendimento conforme o tier do cliente.

O upsell de `rules` para `llm` e esta funcao lendo um campo diferente — sem
deploy, sem migracao, sem fork.
"""

from apps.responder.base import Responder
from apps.responder.rules import RuleResponder
from apps.tenants.models import Configuracao


def responder_para(configuracao: Configuracao) -> Responder:
    if configuracao.responder == Configuracao.Tier.LLM:
        # Import local: o tier de regra nao precisa do SDK da Anthropic
        # carregado para atender.
        from apps.responder.llm.responder import LLMResponder

        return LLMResponder(nome_negocio=configuracao.tenant.nome)
    return RuleResponder()
