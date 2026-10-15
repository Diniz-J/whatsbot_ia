"""Garantias que o banco segura, e nao so o servico.

Duas tarefas da mesma entrega podem rodar ao mesmo tempo. O lock em
`processar_evento` fecha a corrida no caminho normal; as constraints aqui
seguram qualquer caminho que passe por fora dele.
"""

import pytest
from django.db import IntegrityError, transaction

from apps.conversas.models import Contato, Conversa, Mensagem


def test_conversa_duplicada_e_barrada_pelo_banco(restaurante):
    """Sem isto, duas insercoes paralelas quebram todo `get_or_create` depois,
    com MultipleObjectsReturned, para sempre."""
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    Conversa.objects.create(tenant=restaurante, contato=contato)

    with pytest.raises(IntegrityError), transaction.atomic():
        Conversa.objects.create(tenant=restaurante, contato=contato)


def test_mensagem_com_id_repetido_e_barrada_pelo_banco(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(tenant=restaurante, contato=contato)
    Mensagem.objects.create(
        tenant=restaurante,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        id_externo="wamid.AAA",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Mensagem.objects.create(
            tenant=restaurante,
            conversa=conversa,
            direcao=Mensagem.Direcao.ENTRADA,
            id_externo="wamid.AAA",
        )


def test_varias_mensagens_sem_id_externo_convivem(restaurante):
    """Mensagem de saida nasce sem id da Meta: o unique tem que ser parcial,
    senao a segunda resposta do bot colidiria com a primeira."""
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    conversa = Conversa.objects.create(tenant=restaurante, contato=contato)

    for _ in range(2):
        Mensagem.objects.create(
            tenant=restaurante,
            conversa=conversa,
            direcao=Mensagem.Direcao.SAIDA,
            texto="resposta",
        )

    assert Mensagem.objects.filter(id_externo="").count() == 2
