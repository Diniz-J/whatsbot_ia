"""Janela de 24h: ela decide se o envio pode ser texto livre ou template."""

from datetime import timedelta

from django.utils import timezone

from apps.conversas.models import Contato, Conversa


def conversa_com_entrada(tenant, quando):
    contato = Contato.objects.create(tenant=tenant, telefone="5511999999999")
    return Conversa.objects.create(tenant=tenant, contato=contato, ultima_entrada_em=quando)


def test_sem_mensagem_do_cliente_a_janela_esta_fechada(restaurante):
    """Conversa que a escola comecou nunca teve entrada: so template."""
    conversa = conversa_com_entrada(restaurante, None)

    assert conversa.janela_aberta() is False


def test_aberta_pouco_antes_das_24h(restaurante):
    agora = timezone.now()
    conversa = conversa_com_entrada(restaurante, agora - timedelta(hours=23, minutes=59))

    assert conversa.janela_aberta(agora) is True


def test_fechada_pouco_depois_das_24h(restaurante):
    agora = timezone.now()
    conversa = conversa_com_entrada(restaurante, agora - timedelta(hours=24, minutes=1))

    assert conversa.janela_aberta(agora) is False


def test_fechada_exatamente_em_24h(restaurante):
    """No limite exato a Meta ja cobra template; o limite e excludente."""
    agora = timezone.now()
    conversa = conversa_com_entrada(restaurante, agora - timedelta(hours=24))

    assert conversa.janela_aberta(agora) is False
