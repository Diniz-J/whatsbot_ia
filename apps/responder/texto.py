"""Normalizacao de texto para casamento de palavra-chave.

Separado porque e a parte que erra em silencio: sem tirar acento, "cardapio"
nao casa com "cardápio", e o cliente que digitou sem acento cai no fallback
sem ninguem perceber.
"""

import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minusculas, sem acento, sem pontuacao, espaco unico."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    limpo = re.sub(r"[^\w\s]", " ", sem_acento.lower())
    return re.sub(r"\s+", " ", limpo).strip()


def contem_alguma(texto_normalizado: str, termos: tuple[str, ...]) -> bool:
    """Se alguma das expressoes aparece no texto ja normalizado.

    Compara com espaco em volta para "oi" nao casar dentro de "coisa".
    """
    cercado = f" {texto_normalizado} "
    return any(f" {termo} " in cercado for termo in termos)
