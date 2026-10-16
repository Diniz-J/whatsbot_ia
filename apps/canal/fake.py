"""Canal em memoria, para desenvolvimento e teste.

Existe para o pipeline inteiro rodar sem tocar na Meta: o que seria enviado
fica em `enviadas`, e o teste afirma sobre isso em vez de sobre uma chamada
HTTP que ninguem quer fazer na suite.
"""

import uuid

from apps.canal.base import (
    Canal,
    ErroPermanente,
    ErroTransitorio,
    MensagemEntrada,
    StatusEntrega,
)


class CanalFake(Canal):
    def __init__(self, falha: Exception | None = None):
        # Quando setado, todo envio levanta isto. Serve para exercitar os
        # caminhos de retentativa sem simular rede.
        self.falha = falha
        self.enviadas: list[dict] = []

    def normalizar_evento(self, payload: dict) -> list[MensagemEntrada]:
        from apps.canal.whatsapp.canal import CanalWhatsApp

        # O formato de entrada continua sendo o da Meta; so o envio e fingido.
        return CanalWhatsApp().normalizar_evento(payload)

    def normalizar_status(self, payload: dict) -> list[StatusEntrega]:
        from apps.canal.whatsapp.canal import CanalWhatsApp

        return CanalWhatsApp().normalizar_status(payload)

    def enviar_texto(self, telefone: str, texto: str) -> str:
        return self._registrar({"tipo": "texto", "telefone": telefone, "texto": texto})

    def enviar_template(
        self, telefone: str, template: str, idioma: str, variaveis: list[str]
    ) -> str:
        return self._registrar(
            {
                "tipo": "template",
                "telefone": telefone,
                "template": template,
                "idioma": idioma,
                "variaveis": variaveis,
            }
        )

    def _registrar(self, envio: dict) -> str:
        if self.falha is not None:
            raise self.falha
        self.enviadas.append(envio)
        # Id unico entre instancias, e nao um contador por instancia: a
        # fabrica cria um canal por envio, e um contador reiniciado faria
        # todos os envios colidirem no unique de `id_externo` — falha que o
        # canal real nao tem, porque o id vem da Meta.
        return f"wamid.FAKE{uuid.uuid4().hex[:16]}"


# Reexportados para o teste montar falha sem importar de dois lugares.
__all__ = ["CanalFake", "ErroPermanente", "ErroTransitorio"]
