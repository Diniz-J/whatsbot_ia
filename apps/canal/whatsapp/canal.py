"""Adaptador da WhatsApp Cloud API."""

import logging
from datetime import UTC, datetime

from django.conf import settings

from apps.canal.base import Canal, ErroPermanente, MensagemEntrada
from apps.canal.whatsapp.client import ClienteGraph

logger = logging.getLogger(__name__)


def _valores(payload: dict):
    """Percorre os blocos `value` de uma entrega, que vem aninhados.

    O formato da Meta e `entry[].changes[].value`, e uma entrega pode trazer
    mais de um bloco. Tudo que nao for dicionario e descartado: o payload vem
    de fora e nao se confia no formato.
    """
    for entrada in payload.get("entry") or []:
        if not isinstance(entrada, dict):
            continue
        for mudanca in entrada.get("changes") or []:
            if not isinstance(mudanca, dict):
                continue
            valor = mudanca.get("value")
            if isinstance(valor, dict):
                yield valor


def phone_number_ids(payload: dict) -> set[str]:
    """Numeros a que a entrega se destina, para conferir o dono (invariante 3)."""
    return {
        str(valor["metadata"]["phone_number_id"])
        for valor in _valores(payload)
        if isinstance(valor.get("metadata"), dict) and valor["metadata"].get("phone_number_id")
    }


class CanalWhatsApp(Canal):
    """Adaptador da Cloud API: traduz o payload de entrada e envia a saida.

    A configuracao traz o numero e o token do cliente desta instancia. Ela e
    opcional porque normalizar entrada nao precisa de credencial — o webhook
    normaliza antes de resolver o cliente, para saber se ha mensagem na
    entrega. Enviar sem configuracao e erro explicito, nao envio silencioso.
    """

    def __init__(self, configuracao=None):
        self._configuracao = configuracao

    def enviar_texto(self, telefone: str, texto: str) -> str:
        return self._cliente().enviar(
            {
                "messaging_product": "whatsapp",
                "to": telefone,
                "type": "text",
                "text": {"body": texto},
            }
        )

    def enviar_template(
        self, telefone: str, template: str, idioma: str, variaveis: list[str]
    ) -> str:
        componentes = []
        if variaveis:
            componentes.append(
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in variaveis],
                }
            )
        return self._cliente().enviar(
            {
                "messaging_product": "whatsapp",
                "to": telefone,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": idioma},
                    "components": componentes,
                },
            }
        )

    def _cliente(self) -> ClienteGraph:
        configuracao = self._configuracao
        if configuracao is None:
            raise ErroPermanente("canal sem configuracao de cliente: nao da para enviar")
        if not configuracao.whatsapp_phone_number_id or not configuracao.whatsapp_access_token:
            raise ErroPermanente(
                f"cliente {configuracao.tenant_id} sem numero ou token de WhatsApp"
            )
        return ClienteGraph(
            phone_number_id=configuracao.whatsapp_phone_number_id,
            token=configuracao.whatsapp_access_token,
            versao=settings.WHATSAPP_API_VERSION,
            timeout=settings.WHATSAPP_TIMEOUT,
        )

    def normalizar_evento(self, payload: dict) -> list[MensagemEntrada]:
        """Extrai as mensagens de cliente de uma entrega do webhook.

        Status de envio (sent/delivered/read) chegam na mesma URL e sao
        ignorados aqui: pertencem ao lado de saida. Mensagem sem id e
        descartada, porque o id e a chave de idempotencia.
        """
        mensagens: list[MensagemEntrada] = []

        for valor in _valores(payload):
            nomes = self._nomes_por_telefone(valor)

            for bruta in valor.get("messages") or []:
                if not isinstance(bruta, dict):
                    continue
                id_externo = bruta.get("id")
                telefone = bruta.get("from")
                if not id_externo or not telefone:
                    logger.warning("Mensagem sem id ou remetente descartada: %s", bruta.keys())
                    continue

                tipo = bruta.get("type") or "desconhecido"
                mensagens.append(
                    MensagemEntrada(
                        id_externo=str(id_externo),
                        telefone=str(telefone),
                        nome_perfil=nomes.get(str(telefone), ""),
                        tipo=str(tipo),
                        texto=self._texto(bruta, tipo),
                        enviada_em=self._enviada_em(bruta),
                    )
                )

        return mensagens

    @staticmethod
    def _enviada_em(bruta: dict) -> datetime | None:
        """Instante que a Meta informa, em epoch de segundos.

        E ele, e nao a hora em que gravamos, que ancora a janela de 24h: numa
        reentrega atrasada a hora de gravacao esticaria a janela, e num
        processamento fora de ordem ela andaria para tras.
        """
        bruto = bruta.get("timestamp")
        if bruto is None:
            return None
        try:
            return datetime.fromtimestamp(int(bruto), tz=UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            logger.warning("Timestamp invalido na mensagem: %r", bruto)
            return None

    @staticmethod
    def _nomes_por_telefone(valor: dict) -> dict[str, str]:
        """O nome de perfil vem num bloco separado das mensagens, por wa_id."""
        nomes = {}
        for contato in valor.get("contacts") or []:
            if not isinstance(contato, dict):
                continue
            wa_id = contato.get("wa_id")
            perfil = contato.get("profile")
            if wa_id and isinstance(perfil, dict) and perfil.get("name"):
                nomes[str(wa_id)] = str(perfil["name"])
        return nomes

    @staticmethod
    def _texto(bruta: dict, tipo: str) -> str:
        """Texto da mensagem, quando ela tem texto.

        Audio, imagem e documento chegam sem corpo de texto; a mensagem e
        gravada com o tipo e texto vazio, para o processamento decidir o que
        responder sem precisar reinterpretar o payload.
        """
        if tipo == "text":
            corpo = bruta.get("text")
            if isinstance(corpo, dict):
                return str(corpo.get("body") or "")
        elif tipo == "button":
            corpo = bruta.get("button")
            if isinstance(corpo, dict):
                return str(corpo.get("text") or "")
        return ""
