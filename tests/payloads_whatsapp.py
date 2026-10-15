"""Ajudantes para montar entrega de webhook igual a da Meta."""

import hashlib
import hmac
import json
import time

SEGREDO = "segredo-de-teste"


def payload_mensagem(
    phone_number_id="111",
    telefone="5511999999999",
    id_mensagem="wamid.AAA",
    texto="oi, qual o horario de voces?",
    nome="Ze",
    tipo="text",
    timestamp=None,
):
    """Entrega de mensagem de cliente, no formato aninhado da Cloud API.

    O timestamp vale agora por default porque e dele que sai a janela de 24h:
    um valor fixo no passado deixaria a janela fechada e faria o teste de
    janela aberta passar ou falhar por motivo errado.
    """
    if timestamp is None:
        timestamp = int(time.time())
    bruta = {"from": telefone, "id": id_mensagem, "timestamp": str(timestamp), "type": tipo}
    if tipo == "text":
        bruta["text"] = {"body": texto}

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "551140000000",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [{"profile": {"name": nome}, "wa_id": telefone}],
                            "messages": [bruta],
                        },
                    }
                ],
            }
        ],
    }


def payload_status_de_template(phone_number_id="111"):
    """Aprovacao de template: chega na mesma URL e nao tem `metadata`."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "message_template_status_update",
                        "value": {
                            "event": "APPROVED",
                            "message_template_id": 123,
                            "message_template_name": "confirmacao_reserva",
                            "message_template_language": "pt_BR",
                        },
                    }
                ],
            }
        ],
    }


def payload_status(
    phone_number_id="111",
    id_mensagem="wamid.SAIDA",
    status="delivered",
    timestamp=None,
    categoria="service",
    cobravel=True,
    erro=None,
):
    """Entrega de status de envio, que chega na mesma URL das mensagens."""
    if timestamp is None:
        timestamp = int(time.time())
    bruto = {
        "id": id_mensagem,
        "status": status,
        "timestamp": str(timestamp),
        "recipient_id": "5511999999999",
    }
    if categoria is not None:
        bruto["pricing"] = {
            "billable": cobravel,
            "pricing_model": "CBP",
            "category": categoria,
        }
    if erro is not None:
        bruto["errors"] = [{"code": erro[0], "title": erro[1]}]

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "statuses": [bruto],
                        },
                    }
                ],
            }
        ],
    }


def corpo_e_assinatura(payload, segredo=SEGREDO):
    """Serializa uma vez e assina esse byte exato.

    Assinar uma reserializacao diferente da enviada e o jeito mais facil de
    escrever um teste que passa por engano.
    """
    corpo = json.dumps(payload).encode()
    digest = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return corpo, f"sha256={digest}"
