"""
Normalização canônica de identificadores e textos.
Portado da Central (utils/normalize.py) — agnóstico de framework.

Garante que o mesmo dado escrito de formas diferentes em planilhas diferentes
seja tratado como igual (ex: "PROG 10", "PGR 10" e "10" viram a mesma chave).

  - is_blank(v)       → True para vazio/nan/none/n/a.
  - normalize_op(v)   → chave canônica de OP (sem prefixo PROG/PGR/OP). Use SEMPRE
                        ao cruzar OPs entre planilhas.
  - normalize_text(v) → upper + sem acentos + espaços colapsados (nomes/produtos).
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

BLANK_VALUES = frozenset({
    "", "NAN", "NONE", "N/A", "NA", "NAT", "-", "--", "SEM OP", "SEM",
})

_OP_PREFIX_RE = re.compile(
    r"^\s*(?:PROGRAMA(?:CAO|ÇÃO)?|PROG|PGR|O\.?P\.?)\s*[:\-#]?\s*(?=\d)",
    re.IGNORECASE,
)
_SPACES_RE = re.compile(r"\s+")


def is_blank(value) -> bool:
    """True se o valor representa vazio/não-informado."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().upper() in BLANK_VALUES


def normalize_op(value) -> str:
    """Chave canônica de OP para cruzamento entre planilhas (remove prefixo
    PROG/PGR/OP, espaços; normaliza caixa). NÃO é para exibição."""
    if is_blank(value):
        return ""
    s = str(value).strip()
    if re.fullmatch(r"\d+\.0+", s):  # "82.0" → "82"
        s = s.split(".")[0]
    s = _OP_PREFIX_RE.sub("", s)
    s = _SPACES_RE.sub(" ", s).strip()
    s = s.upper()
    if s in BLANK_VALUES:
        return ""
    return s


def normalize_op_series(series: pd.Series) -> pd.Series:
    """Aplica normalize_op a uma Series inteira."""
    return series.map(normalize_op)


def normalize_text(value) -> str:
    """Upper + remove acentos + colapsa espaços. Para nomes/produtos/empresas."""
    if is_blank(value):
        return ""
    s = str(value).strip()
    nfd = unicodedata.normalize("NFD", s)
    s = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    s = _SPACES_RE.sub(" ", s).strip().upper()
    return s
