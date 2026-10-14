"""Conferencia da assinatura HMAC que a Meta manda no webhook."""

import hashlib
import hmac

PREFIXO = "sha256="


def assinatura_valida(corpo: bytes, cabecalho: str | None, segredo: str) -> bool:
    """Confere o `X-Hub-Signature-256` contra o corpo cru da requisicao.

    O corpo tem que ser o cru, byte a byte: qualquer reserializacao do JSON
    muda o digest. A comparacao e de tempo constante para nao vazar o segredo
    pelo tempo de resposta.
    """
    if not cabecalho or not cabecalho.startswith(PREFIXO):
        return False
    if not segredo:
        # Sem segredo configurado nao existe conferencia possivel, e aceitar
        # seria aceitar qualquer payload. Falha fechada.
        return False

    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho[len(PREFIXO) :])
