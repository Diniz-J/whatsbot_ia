"""Canal em memoria, para desenvolvimento e teste.

Existe para o pipeline inteiro rodar sem tocar na Meta: o que seria enviado
fica em `enviadas`, e o teste afirma sobre isso em vez de sobre uma chamada
HTTP que ninguem quer fazer na suite.
"""

import itertools

from apps.canal.base import Canal, ErroPermanente, ErroTransitorio, MensagemEntrada


class CanalFake(Canal):
    def __init__(self, falha: Exception | None = None):
        # Quando setado, todo envio levanta isto. Serve para exercitar os
        # caminhos de retentativa sem simular rede.
        self.falha = falha
        self.enviadas: list[dict] = []
        self._ids = itertools.count(1)

    def normalizar_evento(self, payload: dict) -> list[MensagemEntrada]:
        from apps.canal.whatsapp.canal import CanalWhatsApp

        # O formato de entrada continua sendo o da Meta; so o envio e fingido.
        return CanalWhatsApp().normalizar_evento(payload)

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
        return f"wamid.FAKE{next(self._ids)}"


# Reexportados para o teste montar falha sem importar de dois lugares.
__all__ = ["CanalFake", "ErroPermanente", "ErroTransitorio"]
