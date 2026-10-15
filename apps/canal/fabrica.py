"""Escolha do canal para um cliente.

Existe porque agora ha duas implementacoes do contrato, e quem orquestra nao
deve decidir qual: `core` pede o canal do cliente e recebe o que o ambiente
manda. Em desenvolvimento, `CANAL_EM_MEMORIA` evita falar com a Meta de
verdade sem precisar mexer em codigo.
"""

from django.conf import settings

from apps.canal.base import Canal
from apps.canal.fake import CanalFake
from apps.canal.whatsapp.canal import CanalWhatsApp


def canal_para(configuracao) -> Canal:
    if settings.CANAL_EM_MEMORIA:
        return CanalFake()
    return CanalWhatsApp(configuracao)
