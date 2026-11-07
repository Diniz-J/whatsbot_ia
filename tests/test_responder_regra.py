"""O atendimento por regra: o que ele afirma, e de onde tira.

Testes de unidade do redator. Nada de canal nem HTTP aqui — e o invariante 1
em forma de teste: se um dia o `RuleResponder` precisar de request ou de
cliente de WhatsApp, estes testes deixam de compilar.
"""

from datetime import time
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalogo.models import Categoria, HorarioFuncionamento, Item
from apps.responder.base import Contexto, Pergunta
from apps.responder.rules import RuleResponder

SEGUNDA, SEXTA = 0, 4


@pytest.fixture
def bot():
    return RuleResponder()


@pytest.fixture
def contexto(restaurante):
    return Contexto(tenant_id=restaurante.id, nome_contato="Ze da Silva")


@pytest.fixture
def menu(restaurante):
    pratos = Categoria.objects.create(tenant=restaurante, nome="Pratos", ordem=1)
    bebidas = Categoria.objects.create(tenant=restaurante, nome="Bebidas", ordem=2)
    Item.objects.create(
        tenant=restaurante, categoria=pratos, nome="Picanha", preco=Decimal("89.90")
    )
    Item.objects.create(
        tenant=restaurante, categoria=bebidas, nome="Suco de laranja", preco=Decimal("12.00")
    )
    return {"pratos": pratos, "bebidas": bebidas}


def responder(bot, contexto, texto, tipo="text"):
    return bot.handle(Pergunta(texto=texto, tipo=tipo), contexto)


@pytest.mark.parametrize("texto", ["oi", "Ola!", "bom dia", "Boa noite", "e ai"])
def test_saudacao_se_apresenta_e_usa_o_primeiro_nome(bot, contexto, texto):
    resposta = responder(bot, contexto, texto)

    assert "Ze" in resposta.texto
    assert "Silva" not in resposta.texto
    assert "cardapio" in resposta.texto


def test_saudacao_sem_nome_nao_fica_torta(bot, restaurante):
    resposta = responder(bot, Contexto(tenant_id=restaurante.id), "oi")

    assert resposta.texto.startswith("Oi!")


@pytest.mark.parametrize("nome", [" ", "   ", "\n", "\t"])
def test_saudacao_com_nome_em_branco_nao_estoura(bot, restaurante, nome):
    """O nome vem do perfil do WhatsApp: so espaco e truthy e divide vazio.

    A excecao ali nao ficava contida: ela subia pelo atendimento, a transacao
    do pipeline desfazia o carimbo de `respondida_em` e a varredura escolhia a
    mesma mensagem a cada volta — gastando token em toda uma, no tier de IA.
    """
    resposta = responder(bot, Contexto(tenant_id=restaurante.id, nome_contato=nome), "oi")

    assert resposta.texto.startswith("Oi!")


@pytest.mark.parametrize("texto", ["cardapio", "cardápio", "manda o MENU", "o que tem?"])
def test_cardapio_lista_por_categoria_com_acento_ou_sem(bot, contexto, menu, texto):
    resposta = responder(bot, contexto, texto)

    assert "Pratos" in resposta.texto
    assert "Picanha" in resposta.texto
    assert "R$ 89,90" in resposta.texto
    assert "Bebidas" in resposta.texto


def test_cardapio_esconde_item_indisponivel(bot, contexto, menu):
    Item.objects.filter(nome="Picanha").update(disponivel=False)

    resposta = responder(bot, contexto, "cardapio")

    assert "Picanha" not in resposta.texto


def test_cardapio_vazio_chama_a_equipe_em_vez_de_inventar(bot, contexto):
    resposta = responder(bot, contexto, "cardapio")

    assert "nao tenho o cardapio" in resposta.texto


@pytest.mark.parametrize(
    "texto",
    [
        "quanto custa a picanha",
        "qual o preco da picanha?",
        "preco do picanha",
        "quanto e a Picanha",
        "valor da picanha",
    ],
)
def test_preco_de_item_vem_do_cadastro(bot, contexto, menu, texto):
    resposta = responder(bot, contexto, texto)

    assert "Picanha" in resposta.texto
    assert "R$ 89,90" in resposta.texto


def test_item_sem_preco_cadastrado_nao_recebe_numero_inventado(bot, contexto, menu):
    """O caso que o invariante 2 existe para cobrir: preco em branco nao e
    zero nem chute, e a resposta honesta e dizer que nao sabe."""
    Item.objects.create(
        tenant=menu["pratos"].tenant,
        categoria=menu["pratos"],
        nome="Peixe do dia",
        preco=None,
    )

    resposta = responder(bot, contexto, "quanto custa o peixe do dia")

    assert "nao tenho o preco" in resposta.texto
    assert "R$" not in resposta.texto
    assert "0,00" not in resposta.texto


def test_item_indisponivel_diz_que_acabou_e_nao_que_nao_existe(bot, contexto, menu):
    """ "Temos, mas acabou" e resposta diferente de "nao temos"."""
    Item.objects.filter(nome="Picanha").update(disponivel=False)

    resposta = responder(bot, contexto, "quanto custa a picanha")

    assert "acabou hoje" in resposta.texto
    assert "R$ 89,90" in resposta.texto


def test_item_que_nao_existe_nao_ganha_preco(bot, contexto, menu):
    resposta = responder(bot, contexto, "quanto custa o sushi")

    assert "Nao encontrei" in resposta.texto
    assert "R$" not in resposta.texto


def test_varios_itens_parecidos_pedem_desambiguacao(bot, contexto, menu):
    Item.objects.create(
        tenant=menu["pratos"].tenant,
        categoria=menu["pratos"],
        nome="Picanha ao alho",
        preco=Decimal("99.00"),
    )

    resposta = responder(bot, contexto, "quanto custa a picanha")

    assert "mais de uma opcao" in resposta.texto
    assert "Picanha ao alho" in resposta.texto


def test_pergunta_de_preco_sem_item_cai_no_fallback(bot, contexto, menu):
    """ "quanto" solto nao e busca de item vazia: o fallback explica melhor."""
    resposta = responder(bot, contexto, "quanto")

    assert "Nao entendi" in resposta.texto


def test_horario_quando_aberto(bot, contexto, menu):
    agora = timezone.localtime()
    HorarioFuncionamento.objects.create(
        tenant=menu["pratos"].tenant,
        dia_semana=agora.weekday(),
        abre=time(0, 1),
        fecha=time(23, 59),
    )

    resposta = responder(bot, contexto, "voces estao abertos?")

    assert "abertos agora" in resposta.texto
    assert "00:01" in resposta.texto


def test_horario_quando_fechado_diz_quando_abre(bot, contexto, menu):
    agora = timezone.localtime()
    outro_dia = (agora.weekday() + 2) % 7
    HorarioFuncionamento.objects.create(
        tenant=menu["pratos"].tenant, dia_semana=outro_dia, abre=time(11, 0), fecha=time(15, 0)
    )

    resposta = responder(bot, contexto, "que horas abre")

    assert "fechados agora" in resposta.texto
    assert "11:00" in resposta.texto


def test_sem_horario_cadastrado_chama_a_equipe(bot, contexto):
    resposta = responder(bot, contexto, "que horas abre")

    assert "nao tenho o horario" in resposta.texto


def test_pergunta_vence_a_saudacao_na_mesma_frase(bot, contexto, menu):
    """ "oi, quanto custa a picanha?" e pergunta de preco, nao saudacao."""
    resposta = responder(bot, contexto, "oi, quanto custa a picanha?")

    assert "R$ 89,90" in resposta.texto


@pytest.mark.parametrize("tipo", ["audio", "image", "sticker", "document"])
def test_mensagem_nao_textual_cai_no_fallback(bot, contexto, menu, tipo):
    """Nao ha o que casar num audio; oferecer gente e melhor que chutar."""
    resposta = responder(bot, contexto, "", tipo=tipo)

    assert "Nao entendi" in resposta.texto
    assert "pessoa" in resposta.texto


def test_atendimento_e_escopado_por_cliente(bot, restaurante, clinica, menu):
    """O bot da clinica nao conhece o cardapio do restaurante."""
    resposta = responder(bot, Contexto(tenant_id=clinica.id), "cardapio")

    assert "Picanha" not in resposta.texto


def test_reserva_e_reconhecida_e_encaminhada(restaurante):
    """O tier de regra reconhece o assunto e passa para gente."""
    resposta = RuleResponder().handle(
        Pergunta(texto="queria reservar uma mesa"), Contexto(tenant_id=restaurante.pk)
    )

    assert "equipe" in resposta.texto
    assert "dia" in resposta.texto and "pessoas" in resposta.texto


def test_que_horas_tem_mesa_cai_em_reserva_e_nao_em_horario(restaurante):
    """O "que horas" tambem casa horario de funcionamento; a pergunta e de mesa."""
    resposta = RuleResponder().handle(
        Pergunta(texto="que horas tem mesa livre?"), Contexto(tenant_id=restaurante.pk)
    )

    assert "reserva" in resposta.texto


def test_regra_nunca_afirma_disponibilidade_de_mesa(restaurante):
    """A resposta tem que ser a mesma com o restaurante vazio e com ele lotado.

    E a prova de que o tier de regra nao le disponibilidade: ele nao sabe o
    dia nem o tamanho do grupo, entao qualquer afirmacao sobre mesa seria
    chute — exatamente o que o invariante 2 proibe.
    """
    from datetime import time, timedelta

    from django.utils import timezone

    from apps.catalogo.models import HorarioFuncionamento
    from apps.reservas.models import Mesa, Reserva

    pergunta = Pergunta(texto="tem mesa para 4?")
    contexto = Contexto(tenant_id=restaurante.pk)
    com_mesa_vazia = RuleResponder().handle(pergunta, contexto)

    HorarioFuncionamento.objects.create(
        tenant=restaurante, dia_semana=0, abre=time(19, 0), fecha=time(23, 0)
    )
    mesa = Mesa.objects.create(tenant=restaurante, nome="Mesa 1", capacidade=4)
    Reserva.objects.create(
        tenant=restaurante,
        mesa=mesa,
        nome="Ana",
        pessoas=4,
        inicio=timezone.now() + timedelta(days=1),
    )
    com_mesa_lotada = RuleResponder().handle(pergunta, contexto)

    assert com_mesa_vazia.texto == com_mesa_lotada.texto
    for promessa in ("tem mesa", "ha mesa", "esta reservada", "disponivel"):
        assert promessa not in com_mesa_lotada.texto.lower()
