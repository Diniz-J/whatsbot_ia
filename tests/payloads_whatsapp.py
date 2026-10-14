"""Ajudantes para montar entrega de webhook igual a da Meta."""

import hashlib
import hmac
import json

SEGREDO = "segredo-de-teste"


def payload_mensagem(
    phone_number_id="111",
    telefone="5511999999999",
    id_mensagem="wamid.AAA",
    texto="oi, qual o horario de voces?",
    nome="Ze",
    tipo="text",
):
    """Entrega de mensagem de cliente, no formato aninhado da Cloud API."""
    bruta = {"from": telefone, "id": id_mensagem, "timestamp": "1760000000", "type": tipo}
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


def payload_status(phone_number_id="111", id_mensagem="wamid.SAIDA", status="delivered"):
    """Entrega de status de envio, que chega na mesma URL."""
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
                            "statuses": [
                                {
                                    "id": id_mensagem,
                                    "status": status,
                                    "recipient_id": "5511999999999",
                                }
                            ],
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
