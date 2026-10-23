"""Atendimento por regra: casamento de palavra-chave sobre os servicos.

Nao ha modelo de linguagem aqui, e esse e o ponto: o tier `rules` e o hibrido
com o modulo de IA desligado. Tudo que este arquivo afirma sobre preco,
horario e disponibilidade sai de `catalogo.services` — o mesmo lugar de onde
as tools do LLM vao sair (invariante 2).
"""

import logging
from decimal import Decimal

from apps.catalogo import services as catalogo
from apps.responder.base import Contexto, Pergunta, Responder, Resposta
from apps.responder.texto import contem_alguma, normalizar

logger = logging.getLogger(__name__)

SAUDACOES = ("oi", "ola", "bom dia", "boa tarde", "boa noite", "eai", "e ai")
CARDAPIO = ("cardapio", "menu", "o que tem", "o que voces tem", "opcoes")
# Plurais listados um a um de proposito: o casamento e por palavra inteira,
# para "oi" nao casar dentro de "coisa", e "aberto" nao pega "abertos".
HORARIO = (
    "horario",
    "horarios",
    "que horas",
    "abre",
    "abrem",
    "fecha",
    "fecham",
    "aberto",
    "abertos",
    "aberta",
    "fechado",
    "fechados",
    "funcionamento",
    "funciona",
    "funcionam",
)
RESERVA = (
    "reserva",
    "reservas",
    "reservar",
    "mesa",
    "mesas",
    "lugar",
    "lugares",
)
# Gatilhos de pergunta de preco, do mais longo para o mais curto: o termo do
# item e o que vem DEPOIS do gatilho. Extrair pela cauda e nao por remocao de
# ruido importa porque o nome do item pode conter as mesmas palavras — tirar
# todo "do" da frase quebraria "Peixe do dia".
GATILHOS_DE_PRECO = tuple(
    sorted(
        (
            "quanto custa o",
            "quanto custa a",
            "quanto custam",
            "quanto custa",
            "qual o preco do",
            "qual o preco da",
            "qual o preco de",
            "qual o preco",
            "qual o valor do",
            "qual o valor da",
            "qual o valor",
            "preco do",
            "preco da",
            "preco de",
            "preco",
            "precos",
            "valor do",
            "valor da",
            "valor",
            "quanto e o",
            "quanto e a",
            "quanto e",
            "quanto",
        ),
        key=len,
        reverse=True,
    )
)

# Artigos e preposicoes tirados apenas do inicio do termo, nunca do meio.
ARTIGOS = ("o", "a", "os", "as", "um", "uma", "de", "do", "da")

DIAS_CURTOS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom")


class RuleResponder(Responder):
    def handle(self, pergunta: Pergunta, contexto: Contexto) -> Resposta | None:
        if pergunta.tipo != "text" or not pergunta.texto.strip():
            # Audio, imagem e figurinha nao tem o que casar. Encaminhar para
            # gente e melhor que responder qualquer coisa.
            return Resposta(self._nao_entendi(contexto))

        texto = normalizar(pergunta.texto)

        # Ordem importa: "quanto custa a picanha" tem que cair em preco, nao
        # em cardapio, e a saudacao so vale quando vem sozinha.
        if self._pede_preco(texto):
            return Resposta(self._responder_preco(texto, contexto))
        # Antes de horario de proposito: "que horas tem mesa" e pergunta de
        # reserva, e cairia no horario de funcionamento pelo "que horas".
        if contem_alguma(texto, RESERVA):
            return Resposta(self._responder_reserva())
        if contem_alguma(texto, CARDAPIO):
            return Resposta(self._responder_cardapio(contexto))
        if contem_alguma(texto, HORARIO):
            return Resposta(self._responder_horario(contexto))
        if contem_alguma(texto, SAUDACOES):
            return Resposta(self._saudar(contexto))
        return Resposta(self._nao_entendi(contexto))

    @staticmethod
    def _pede_preco(texto: str) -> bool:
        """Preco so quando sobra algo para procurar depois de tirar o ruido.

        Sem isso, um "quanto" solto viraria busca de item vazia e cairia num
        "nao achei" confuso, quando o fallback explica melhor.
        """
        if not contem_alguma(texto, GATILHOS_DE_PRECO):
            return False
        return bool(_termo_do_item(texto))

    def _responder_preco(self, texto: str, contexto: Contexto) -> str:
        termo = _termo_do_item(texto)
        achados = catalogo.buscar_itens(contexto.tenant_id, termo, incluir_indisponiveis=True)

        if not achados:
            return f"Nao encontrei '{termo}' no nosso cardapio. Quer ver o cardapio completo?"

        if len(achados) > 1:
            nomes = ", ".join(i.nome for i in achados[:5])
            return f"Achei mais de uma opcao com '{termo}': {nomes}. Qual delas?"

        item = achados[0]
        if item.preco is None:
            # Invariante 2: preco em branco significa sem preco cadastrado.
            # Dizer que nao sabe e a unica resposta honesta; inventar um
            # numero ou responder zero seria pior que nao responder.
            return (
                f"{item.nome} esta no cardapio, mas nao tenho o preco aqui. "
                "Vou confirmar com a equipe."
            )
        if not item.disponivel:
            return f"{item.nome} sai por {_moeda(item.preco)}, mas acabou hoje."
        return f"{item.nome} sai por {_moeda(item.preco)}."

    def _responder_cardapio(self, contexto: Contexto) -> str:
        secoes = catalogo.cardapio(contexto.tenant_id)
        if not secoes:
            return "Ainda nao tenho o cardapio cadastrado aqui. Vou chamar a equipe."

        linhas = []
        for categoria, itens in secoes:
            linhas.append(f"*{categoria.nome}*")
            for item in itens:
                preco = _moeda(item.preco) if item.preco is not None else "a consultar"
                linhas.append(f"- {item.nome}: {preco}")
        return "\n".join(linhas)

    def _responder_horario(self, contexto: Contexto) -> str:
        faixas = catalogo.horarios(contexto.tenant_id)
        if not faixas:
            return "Ainda nao tenho o horario cadastrado aqui. Vou chamar a equipe."

        if catalogo.esta_aberto(contexto.tenant_id):
            return f"Estamos abertos agora. Nosso horario:\n{_formatar_horarios(faixas)}"

        abertura = catalogo.proxima_abertura(contexto.tenant_id)
        if abertura is None:
            return f"Estamos fechados agora. Nosso horario:\n{_formatar_horarios(faixas)}"
        return (
            f"Estamos fechados agora. Abrimos {DIAS_CURTOS[abertura.weekday()]} "
            f"as {abertura:%H:%M}.\n{_formatar_horarios(faixas)}"
        )

    @staticmethod
    def _responder_reserva() -> str:
        """Reconhece o assunto e passa para gente. Nunca afirma disponibilidade.

        O tier de regra casa palavra-chave numa mensagem isolada: nao extrai
        dia nem numero de pessoas, e nao tem o fio da conversa para pedir o
        que falta. Dizer "temos mesa" sem consultar seria exatamente o que o
        invariante 2 proibe, e consultar sem saber o horario e o tamanho do
        grupo nao e consultar nada.
        """
        return (
            "Consigo te passar para alguem da equipe fechar a reserva. "
            "Me diga o dia, o horario e quantas pessoas."
        )

    @staticmethod
    def _saudar(contexto: Contexto) -> str:
        # `split` e nao `split()[0]` direto: o nome vem do perfil do WhatsApp,
        # entao um nome so de espaco e truthy e sai da divisao vazio. A
        # excecao ali derrubava o atendimento, a transacao desfazia o carimbo
        # de respondida e a varredura reatendia a mesma mensagem a cada volta.
        partes = (contexto.nome_contato or "").split()
        nome = f" {partes[0]}" if partes else ""
        return (
            f"Oi{nome}! Posso te passar o cardapio, o preco de um item, o nosso horario "
            "ou falar de reserva. O que voce precisa?"
        )

    @staticmethod
    def _nao_entendi(contexto: Contexto) -> str:
        return (
            "Nao entendi. Consigo te passar o cardapio, o preco de um item, o nosso "
            "horario de funcionamento ou falar de reserva. Se preferir falar com uma "
            "pessoa, e so dizer."
        )


def _termo_do_item(texto: str) -> str:
    """O nome do item: o que vem depois do gatilho de preco.

    Pela cauda e nao por remocao de ruido, para o que o cliente escreveu antes
    da pergunta ("oi, quanto custa...") nao entrar no termo, e para as
    palavras do proprio nome sobreviverem.
    """
    for gatilho in GATILHOS_DE_PRECO:
        posicao = texto.find(gatilho)
        if posicao == -1:
            continue
        return _sem_artigo_inicial(texto[posicao + len(gatilho) :].strip())
    return ""


def _sem_artigo_inicial(texto: str) -> str:
    palavras = texto.split()
    while palavras and palavras[0] in ARTIGOS:
        palavras.pop(0)
    return " ".join(palavras)


def _moeda(valor: Decimal) -> str:
    return f"R$ {valor:.2f}".replace(".", ",")


def _formatar_horarios(faixas) -> str:
    return "\n".join(
        f"{DIAS_CURTOS[f.dia_semana]}: {f.abre:%H:%M} as {f.fecha:%H:%M}" for f in faixas
    )
