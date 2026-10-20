"""Teto de gasto por cliente.

O `metricas` media o custo e nao conseguia parar nada. Este e o freio: passando
do teto, o atendimento volta para o tier de regra — o cliente final continua
sendo atendido, pior, e a conta para de crescer.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.conversas.models import Contato, Conversa, Mensagem
from apps.responder.llm.responder import LLMResponder
from apps.responder.registry import responder_para
from apps.responder.rules import RuleResponder
from apps.tenants.models import Configuracao


@pytest.fixture
def no_tier_llm(restaurante):
    configuracao = restaurante.configuracao
    configuracao.responder = Configuracao.Tier.LLM
    configuracao.save(update_fields=["responder", "atualizado_em"])
    return configuracao


@pytest.fixture
def conversa(restaurante):
    contato = Contato.objects.create(tenant=restaurante, telefone="5511999999999")
    return Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )


def gastar(conversa, tokens, quando=None):
    mensagem = Mensagem.objects.create(
        tenant=conversa.tenant,
        conversa=conversa,
        direcao=Mensagem.Direcao.SAIDA,
        texto="resposta",
        tokens_entrada=tokens,
        tokens_saida=0,
    )
    if quando is not None:
        Mensagem.objects.filter(pk=mensagem.pk).update(criado_em=quando)
    return mensagem


def test_sem_teto_configurado_o_tier_llm_vale(no_tier_llm, conversa, settings):
    """Nasce desligado: o numero depende do volume real do cliente."""
    settings.LLM_TETO_TOKENS_MES = 0
    gastar(conversa, 10_000_000)

    assert isinstance(responder_para(no_tier_llm), LLMResponder)


def test_abaixo_do_teto_o_tier_llm_vale(no_tier_llm, conversa, settings):
    settings.LLM_TETO_TOKENS_MES = 1000
    gastar(conversa, 999)

    assert isinstance(responder_para(no_tier_llm), LLMResponder)


def test_no_teto_cai_na_regra(no_tier_llm, conversa, settings):
    """Teto alcancado e teto estourado: o freio pega no igual tambem."""
    settings.LLM_TETO_TOKENS_MES = 1000
    gastar(conversa, 1000)

    assert isinstance(responder_para(no_tier_llm), RuleResponder)


def test_acima_do_teto_cai_na_regra_e_nao_para_de_atender(no_tier_llm, conversa, settings):
    """Atender pior e melhor que nao atender: o cliente final nao tem culpa
    do orcamento, e o dono paga por um atendimento que funciona."""
    settings.LLM_TETO_TOKENS_MES = 1000
    gastar(conversa, 5000)

    atendimento = responder_para(no_tier_llm)

    assert isinstance(atendimento, RuleResponder)
    assert atendimento is not None


def test_o_teto_conta_so_o_mes_corrente(no_tier_llm, conversa, settings):
    """Mes novo, orcamento novo: gasto do mes passado nao segue freando."""
    settings.LLM_TETO_TOKENS_MES = 1000
    mes_passado = timezone.now() - timedelta(days=40)
    gastar(conversa, 5000, quando=mes_passado)

    assert isinstance(responder_para(no_tier_llm), LLMResponder)


def test_o_teto_e_por_cliente(clinica, no_tier_llm, conversa, settings):
    """Gasto do vizinho nao pode frear quem nao gastou."""
    settings.LLM_TETO_TOKENS_MES = 1000
    gastar(conversa, 5000)

    configuracao_vizinha = clinica.configuracao
    configuracao_vizinha.responder = Configuracao.Tier.LLM
    configuracao_vizinha.save(update_fields=["responder", "atualizado_em"])

    assert isinstance(responder_para(no_tier_llm), RuleResponder)
    assert isinstance(responder_para(configuracao_vizinha), LLMResponder)


def test_tier_de_regra_nao_consulta_gasto(
    restaurante, conversa, settings, django_assert_num_queries
):
    """A regra nao custa token, entao conferir orcamento nela seria consulta
    por mensagem sem motivo."""
    settings.LLM_TETO_TOKENS_MES = 1000
    gastar(conversa, 5000)

    with django_assert_num_queries(0):
        assert isinstance(responder_para(restaurante.configuracao), RuleResponder)


def test_o_teto_custa_uma_consulta(no_tier_llm, conversa, settings, django_assert_num_queries):
    settings.LLM_TETO_TOKENS_MES = 1000
    gastar(conversa, 10)

    with django_assert_num_queries(1):
        responder_para(no_tier_llm)


def test_ponta_a_ponta_estourado_nao_chama_a_api(no_tier_llm, conversa, monkeypatch, settings):
    """A prova que importa: passando do teto, nenhuma chamada cobrada sai.

    Travar so a escolha do tier deixaria de fora o que o teto existe para
    fazer, que e nao gastar.
    """
    from apps.canal.fake import CanalFake
    from apps.core import tasks
    from apps.core.pipeline import responder_mensagem
    from apps.responder.llm import cliente as modulo_cliente
    from tests.fake_anthropic import AnthropicFalso, BlocoTexto, RespostaFalsa

    settings.LLM_TETO_TOKENS_MES = 1000
    settings.ANTHROPIC_API_KEY = "chave-de-teste"
    monkeypatch.setattr(tasks, "canal_para", lambda configuracao: CanalFake())
    construir = AnthropicFalso.com([RespostaFalsa([BlocoTexto("resposta de IA")])])
    monkeypatch.setattr(modulo_cliente.anthropic, "Anthropic", construir)
    gastar(conversa, 5000)

    entrada = Mensagem.objects.create(
        tenant=conversa.tenant,
        conversa=conversa,
        direcao=Mensagem.Direcao.ENTRADA,
        texto="qual o horario?",
    )
    saida = responder_mensagem(entrada.pk)

    assert construir.mensagens.chamadas == []
    assert saida is not None
    assert "resposta de IA" not in saida.texto
    # Resposta de regra nao mede token; gravar zero confundiria com gasto real.
    assert saida.tokens_entrada is None
