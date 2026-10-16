"""Escolha do atendimento conforme o tier do cliente.

O upsell de `rules` para `llm` e esta funcao lendo um campo diferente — sem
deploy, sem migracao, sem fork. O `LLMResponder` entra aqui quando existir.
"""

from apps.responder.base import Responder
from apps.responder.rules import RuleResponder
from apps.tenants.models import Configuracao


def responder_para(configuracao: Configuracao) -> Responder:
    if configuracao.responder == Configuracao.Tier.LLM:
        # Ainda nao implementado: cair no de regra e melhor que nao atender,
        # e o log diz por que a resposta saiu mais simples do que o plano.
        import logging

        logging.getLogger(__name__).warning(
            "Cliente %s esta no tier llm, que ainda nao existe: atendendo por regra",
            configuracao.tenant_id,
        )
    return RuleResponder()
