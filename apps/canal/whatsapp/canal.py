"""Adaptador da WhatsApp Cloud API."""

import logging
from datetime import UTC, datetime

from django.conf import settings

from apps.canal.base import Canal, ErroPermanente, MensagemEntrada, StatusEntrega
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


def _instante(bruto) -> datetime | None:
    """Epoch de segundos da Meta em datetime consciente de fuso.

    E ele, e nao a hora em que gravamos, que ancora a janela de 24h: numa
    reentrega atrasada a hora de gravacao esticaria a janela, e num
    processamento fora de ordem ela andaria para tras.
    """
    if bruto is None:
        return None
    try:
        return datetime.fromtimestamp(int(bruto), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        logger.warning("Timestamp invalido no payload: %r", bruto)
        return None


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

    def normalizar_status(self, payload: dict) -> list[StatusEntrega]:
        """Extrai os status de envio de uma entrega do webhook.

        Chegam na mesma URL das mensagens de cliente, em `statuses[]`. Status
        sem id e descartado: o id e o unico jeito de achar a mensagem.
        """
        status = []
        for valor in _valores(payload):
            for bruto in valor.get("statuses") or []:
                if not isinstance(bruto, dict):
                    continue
                id_externo = bruto.get("id")
                nome = bruto.get("status")
                if not id_externo or not nome:
                    logger.warning("Status sem id ou sem nome descartado")
                    continue
                preco = bruto.get("pricing") if isinstance(bruto.get("pricing"), dict) else {}
                status.append(
                    StatusEntrega(
                        id_externo=str(id_externo),
                        status=str(nome),
                        momento=_instante(bruto.get("timestamp")),
                        erro=self._erro(bruto),
                        categoria=str(preco.get("category") or ""),
                        cobravel=preco.get("billable") if "billable" in preco else None,
                    )
                )
        return status

    @staticmethod
    def _erro(bruto: dict) -> str:
        """Motivo da falha, quando o status e `failed`.

        Guardado como texto porque o que interessa e a pessoa que le a
        conversa entender por que nao chegou, nao o codigo em si.
        """
        erros = bruto.get("errors")
        if not isinstance(erros, list) or not erros:
            return ""
        primeiro = erros[0]
        if not isinstance(primeiro, dict):
            return ""
        return f"{primeiro.get('code')} {primeiro.get('title') or primeiro.get('message')}".strip()

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
                        enviada_em=_instante(bruta.get("timestamp")),
                    )
                )

        return mensagens

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
