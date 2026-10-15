"""A tarefa de envio: reserva, entrega, e o que faz com cada tipo de falha."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.canal.base import ErroPermanente, ErroTransitorio
from apps.canal.fake import CanalFake
from apps.conversas.models import Contato, Conversa, Mensagem
from apps.core import tasks
from apps.core.envio import agendar_template, agendar_texto


@pytest.fixture
def conversa(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    return Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )


@pytest.fixture
def canal(monkeypatch):
    """Injeta um canal em memoria no lugar da Meta."""
    falso = CanalFake()
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: falso)
    return falso


def test_envia_texto_e_guarda_o_id(conversa, canal):
    mensagem = agendar_texto(conversa, "mesa reservada")

    id_externo = tasks.enviar_outbound(mensagem.pk)
    mensagem.refresh_from_db()

    assert canal.enviadas == [
        {"tipo": "texto", "telefone": "5511999999999", "texto": "mesa reservada"}
    ]
    assert mensagem.status == Mensagem.Status.ENVIADA
    assert mensagem.id_externo == id_externo != ""


def test_envia_template_com_idioma_e_variaveis(conversa, canal):
    mensagem = agendar_template(conversa, "confirmacao_reserva", variaveis=["20h"])

    tasks.enviar_outbound(mensagem.pk, idioma="pt_BR", variaveis=["20h"])

    assert canal.enviadas == [
        {
            "tipo": "template",
            "telefone": "5511999999999",
            "template": "confirmacao_reserva",
            "idioma": "pt_BR",
            "variaveis": ["20h"],
        }
    ]


def test_falha_transitoria_devolve_a_mensagem_para_a_fila(conversa, monkeypatch):
    """Sem destravar o status, a retentativa nao conseguiria reservar."""
    monkeypatch.setattr(tasks, "canal_para", lambda c: CanalFake(falha=ErroTransitorio("503")))
    mensagem = agendar_texto(conversa, "oi")

    with pytest.raises(ErroTransitorio):
        tasks.enviar_outbound(mensagem.pk)

    mensagem.refresh_from_db()
    assert mensagem.status == Mensagem.Status.ENFILEIRADA
    assert mensagem.id_externo == ""


def test_falha_permanente_nao_retenta_e_registra_o_motivo(conversa, monkeypatch):
    """Repetir numero invalido queima cota e nunca passa."""
    monkeypatch.setattr(
        tasks, "canal_para", lambda c: CanalFake(falha=ErroPermanente("131026 undeliverable"))
    )
    mensagem = agendar_texto(conversa, "oi")

    assert tasks.enviar_outbound(mensagem.pk) is None

    mensagem.refresh_from_db()
    assert mensagem.status == Mensagem.Status.FALHOU
    assert "131026" in mensagem.erro


def test_mensagem_ja_enviada_nao_e_enviada_de_novo(conversa, canal):
    """Retentativa depois de um envio bem-sucedido nao pode cobrar duas vezes."""
    mensagem = agendar_texto(conversa, "oi")
    tasks.enviar_outbound(mensagem.pk)

    assert tasks.enviar_outbound(mensagem.pk) is None
    assert len(canal.enviadas) == 1


def test_mensagem_reservada_por_outra_execucao_e_ignorada(conversa, canal):
    """O claim e o que impede duas execucoes de mandarem a mesma mensagem."""
    mensagem = agendar_texto(conversa, "oi")
    Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.ENVIANDO)

    assert tasks.enviar_outbound(mensagem.pk) is None
    assert canal.enviadas == []


def test_mensagem_inexistente_nao_explode(conversa, canal):
    assert tasks.enviar_outbound(999999) is None


def test_cliente_sem_configuracao_e_falha_permanente(restaurante, canal):
    """Sem configuracao nao ha para onde mandar, e repetir nao a cria."""
    from apps.tenants.models import Configuracao, Tenant

    outro = Tenant.objects.create(nome="Sem Config", slug="sem-config")
    assert not Configuracao.objects.filter(tenant=outro).exists()
    contato = Contato.objects.create(tenant=outro, telefone="5511966666666")
    conversa_sem = Conversa.objects.create(
        tenant=outro, contato=contato, ultima_entrada_em=timezone.now()
    )
    mensagem = agendar_texto(conversa_sem, "oi")

    assert tasks.enviar_outbound(mensagem.pk) is None

    mensagem.refresh_from_db()
    assert mensagem.status == Mensagem.Status.FALHOU
    assert "configuracao" in mensagem.erro


def test_janela_nao_e_reavaliada_no_envio(conversa, canal):
    """A janela e regra de agendamento. Se ela fechasse entre agendar e
    enviar, recusar aqui deixaria a mensagem presa sem ninguem decidindo."""
    mensagem = agendar_texto(conversa, "oi")
    Conversa.objects.filter(pk=conversa.pk).update(
        ultima_entrada_em=timezone.now() - timedelta(hours=30)
    )

    tasks.enviar_outbound(mensagem.pk)

    mensagem.refresh_from_db()
    assert mensagem.status == Mensagem.Status.ENVIADA
