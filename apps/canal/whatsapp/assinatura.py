"""Conferencia da assinatura HMAC que a Meta manda no webhook."""

import hashlib
import hmac

PREFIXO = "sha256="
# sha256 em hexadecimal.
TAMANHO_HEX = 64


def assinatura_valida(corpo: bytes, cabecalho: str | None, segredo: str) -> bool:
    """Confere o `X-Hub-Signature-256` contra o corpo cru da requisicao.

    O corpo tem que ser o cru, byte a byte: qualquer reserializacao do JSON
    muda o digest. A comparacao e de tempo constante para nao vazar o segredo
    pelo tempo de resposta, e e feita sobre os bytes do digest e nao sobre o
    texto do cabecalho: `compare_digest` levanta `TypeError` com `str`
    nao-ASCII, e esse erro num endpoint sem autenticacao viraria 500 em vez
    do 403 que o caminho inteiro foi escrito para dar.
    """
    if not cabecalho or not cabecalho.startswith(PREFIXO):
        return False
    if not segredo:
        # Sem segredo configurado nao existe conferencia possivel, e aceitar
        # seria aceitar qualquer payload. Falha fechada.
        return False

    recebido = cabecalho[len(PREFIXO) :]
    if len(recebido) != TAMANHO_HEX:
        return False
    try:
        recebido_bytes = bytes.fromhex(recebido)
    except ValueError:
        return False

    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).digest()
    return hmac.compare_digest(esperado, recebido_bytes)


def token_valido(recebido: str | None, esperado: str) -> bool:
    """Confere o token de verificacao do webhook.

    Mesma comparacao de tempo constante sobre bytes, pelo mesmo motivo do
    `assinatura_valida`: o token chega de fora e pode nao ser ASCII.
    """
    if not recebido or not esperado:
        return False
    return hmac.compare_digest(recebido.encode(), esperado.encode())
