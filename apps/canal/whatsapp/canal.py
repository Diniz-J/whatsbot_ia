"""Adaptador da WhatsApp Cloud API."""

import logging

from apps.canal.base import Canal, MensagemEntrada

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
    """Traduz o payload da Cloud API para o vocabulario do dominio."""

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
