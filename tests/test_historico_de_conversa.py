"""O historico que o atendimento recebe.

Multi-turno depende disto, e errar o recorte sai caro das duas pontas: de
menos e o atendimento nao lembra da propria pergunta, de mais e token de
entrada pago em toda mensagem. Mentir tambem custa — uma saida que falhou no
historico faz o atendimento responder em cima de algo que o cliente nunca leu.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.conversas.models import JANELA_SERVICO, Contato, Conversa, Mensagem
from apps.core.pipeline import _historico
from apps.responder.base import Autor


@pytest.fixture
def conversa(restaurante):
    contato = Contato.objects.create(
        tenant=restaurante, telefone="5511999999999", nome_perfil="Ana"
    )
    return Conversa.objects.create(
        tenant=restaurante, contato=contato, ultima_entrada_em=timezone.now()
    )


def falar(conversa, direcao, texto, atras=timedelta(0), **extras):
    """Cria uma mensagem `atras` no tempo. `criado_em` e `auto_now_add`."""
    if direcao == Mensagem.Direcao.SAIDA:
        extras.setdefault("status", Mensagem.Status.ENVIADA)
    mensagem = Mensagem.objects.create(
        tenant=conversa.tenant, conversa=conversa, direcao=direcao, texto=texto, **extras
    )
    Mensagem.objects.filter(pk=mensagem.pk).update(criado_em=timezone.now() - atras)
    mensagem.refresh_from_db()
    return mensagem


def test_historico_vem_do_mais_antigo_para_o_mais_novo(conversa):
    falar(conversa, Mensagem.Direcao.ENTRADA, "tem mesa sabado?", atras=timedelta(minutes=10))
    falar(conversa, Mensagem.Direcao.SAIDA, "para quantas pessoas?", atras=timedelta(minutes=9))
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "quatro")

    historico = _historico(atual)

    assert [(t.autor, t.texto) for t in historico] == [
        (Autor.CLIENTE, "tem mesa sabado?"),
        (Autor.ATENDENTE, "para quantas pessoas?"),
    ]


def test_a_pergunta_atual_nao_entra_no_proprio_historico(conversa):
    """Ela vai como o ultimo turno, por quem monta a chamada."""
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "oi")

    assert _historico(atual) == ()


def test_primeiro_contato_tem_historico_vazio(conversa):
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "bom dia")

    assert _historico(atual) == ()


def test_saida_que_falhou_fica_fora(conversa):
    """O cliente nunca leu; responder em cima disso seria responder sozinho."""
    falar(
        conversa,
        Mensagem.Direcao.SAIDA,
        "segue o cardapio",
        atras=timedelta(minutes=5),
        status=Mensagem.Status.FALHOU,
    )
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "e ai?")

    assert _historico(atual) == ()


def test_saida_ainda_na_fila_fica_fora(conversa):
    falar(
        conversa,
        Mensagem.Direcao.SAIDA,
        "ja te respondo",
        atras=timedelta(minutes=5),
        status=Mensagem.Status.ENFILEIRADA,
    )
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "e ai?")

    assert _historico(atual) == ()


def test_saida_entregue_e_lida_entram(conversa):
    falar(
        conversa,
        Mensagem.Direcao.SAIDA,
        "abrimos as 19h",
        atras=timedelta(minutes=6),
        status=Mensagem.Status.ENTREGUE,
    )
    falar(
        conversa,
        Mensagem.Direcao.SAIDA,
        "quer reservar?",
        atras=timedelta(minutes=5),
        status=Mensagem.Status.LIDA,
    )
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "quero")

    assert [t.texto for t in _historico(atual)] == ["abrimos as 19h", "quer reservar?"]


def test_mensagem_sem_texto_fica_fora(conversa):
    """Audio, imagem e template puro nao tem fala para reproduzir."""
    falar(conversa, Mensagem.Direcao.ENTRADA, "", atras=timedelta(minutes=5), tipo="audio")
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "ouviu?")

    assert _historico(atual) == ()


def test_fala_fora_da_janela_de_servico_nao_volta(conversa):
    """Conversa de ontem nao e contexto da de hoje — e seria token pago."""
    falar(
        conversa,
        Mensagem.Direcao.ENTRADA,
        "qual o preco da picanha?",
        atras=JANELA_SERVICO + timedelta(minutes=1),
    )
    falar(conversa, Mensagem.Direcao.ENTRADA, "e hoje?", atras=timedelta(minutes=1))
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "oi")

    assert [t.texto for t in _historico(atual)] == ["e hoje?"]


def test_teto_guarda_o_fim_da_conversa_e_nao_o_comeco(conversa, settings):
    """Cortar antes de reordenar: o que importa e a fala recente."""
    settings.HISTORICO_MAX_TURNOS = 3
    for numero in range(6):
        falar(
            conversa,
            Mensagem.Direcao.ENTRADA,
            f"fala {numero}",
            atras=timedelta(minutes=10 - numero),
        )
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "ultima")

    assert [t.texto for t in _historico(atual)] == ["fala 3", "fala 4", "fala 5"]


def test_historico_nao_vaza_de_outra_conversa(conversa, restaurante):
    outro = Contato.objects.create(tenant=restaurante, telefone="5511888888888")
    vizinha = Conversa.objects.create(
        tenant=restaurante, contato=outro, ultima_entrada_em=timezone.now()
    )
    falar(vizinha, Mensagem.Direcao.ENTRADA, "segredo do vizinho", atras=timedelta(minutes=5))
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "oi")

    assert _historico(atual) == ()


def test_historico_cabe_numa_consulta(conversa, django_assert_num_queries):
    falar(conversa, Mensagem.Direcao.ENTRADA, "tem mesa?", atras=timedelta(minutes=5))
    falar(conversa, Mensagem.Direcao.SAIDA, "para quantos?", atras=timedelta(minutes=4))
    atual = falar(conversa, Mensagem.Direcao.ENTRADA, "dois")

    with django_assert_num_queries(1):
        assert len(_historico(atual)) == 2


def test_tier_de_regra_responde_igual_com_e_sem_historico(restaurante):
    """A regra casa palavra-chave na mensagem atual e ignora o resto.

    Se o historico mudasse a resposta dela, teria virado outra coisa sem
    ninguem pedir.
    """
    from apps.responder.base import Contexto, Pergunta, Turno
    from apps.responder.rules import RuleResponder

    pergunta = Pergunta(texto="qual o horario?")
    sem = RuleResponder().handle(pergunta, Contexto(tenant_id=restaurante.pk))
    com = RuleResponder().handle(
        pergunta,
        Contexto(
            tenant_id=restaurante.pk,
            historico=(
                Turno(Autor.CLIENTE, "tem mesa?"),
                Turno(Autor.ATENDENTE, "para quantas pessoas?"),
            ),
        ),
    )

    assert sem.texto == com.texto
