"""
Parser de datas robusto com detecção de formato por COLUNA.
Portado da Central (utils/date_parser.py) — agnóstico de framework.

O Google Sheets exporta datas conforme o locale de cada planilha (M/D/YYYY,
D/M/YY, etc.). Um parser que decide valor-a-valor erra datas ambíguas. Aqui a
ordem é detectada varrendo a coluna inteira e aplicada de forma consistente.
Use SEMPRE parse_date_series ao carregar uma coluna de datas de planilha.
"""

from __future__ import annotations

import re

import pandas as pd

_DATE_RE = re.compile(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$")
_ISO_RE = re.compile(r"^\s*(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})")


def _only_date_part(value) -> str:
    """Remove parte de hora ('5/28/2026 00:00:00' → '5/28/2026')."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "nat", ""):
        return ""
    return s.split(" ")[0].strip()


def detectar_ordem(series: pd.Series, default: str = "DMY") -> str:
    """Retorna 'DMY' (dia primeiro), 'MDY' (mês primeiro) ou 'ISO' (ano primeiro).

    default: ordem assumida quando todas as datas são ambíguas (ambos ≤ 12).
    Use "MDY" para planilhas com locale US (ex: LITTEX, GGTTEX).

    Decide por MAIORIA de evidência inequívoca (não pela primeira encontrada):
    colunas de planilhas compartilhadas por vários prestadores/abas às vezes têm
    algumas poucas linhas digitadas manualmente no formato "errado" (ex.: 1 ou 2
    linhas em D/M dentro de uma coluna majoritariamente M/D) — deixar UMA linha
    isolada decidir a ordem da coluna inteira inverte silenciosamente o mês de
    todas as datas ambíguas (dia ≤ 12) das demais linhas."""
    tem_iso = False
    votos_dmy = 0  # 1ª posição > 12 → só pode ser dia → coluna é D/M
    votos_mdy = 0  # 2ª posição > 12 → só pode ser dia → coluna é M/D

    for raw in series.dropna():
        s = _only_date_part(raw)
        if not s:
            continue
        if _ISO_RE.match(s):
            tem_iso = True
            continue
        m = _DATE_RE.match(s)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            votos_dmy += 1
        if b > 12:
            votos_mdy += 1

    if tem_iso and not votos_dmy and not votos_mdy:
        return "ISO"
    if votos_dmy or votos_mdy:
        return "DMY" if votos_dmy > votos_mdy else "MDY"
    return default


def _parse_um(s: str, ordem: str) -> pd.Timestamp:
    """Converte um único valor já sabendo a ordem da coluna."""
    if not s:
        return pd.NaT

    mi = _ISO_RE.match(s)
    if mi:
        try:
            return pd.to_datetime(s.split(" ")[0], format="%Y-%m-%d")
        except Exception:
            return pd.to_datetime(s, errors="coerce")

    m = _DATE_RE.match(s)
    if not m:
        return pd.to_datetime(s, errors="coerce")

    a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(y) == 2:
        y = "20" + y
    year = int(y)

    if ordem == "MDY":
        mes, dia = a, b
    else:  # DMY
        dia, mes = a, b

    if mes > 12 and dia <= 12:
        dia, mes = mes, dia

    try:
        return pd.Timestamp(year=year, month=mes, day=dia)
    except (ValueError, TypeError):
        return pd.NaT


def parse_date_series(series: pd.Series, default_order: str = "DMY") -> pd.Series:
    """Converte Series de datas (strings) em datetime, detectando o formato da
    coluna inteira primeiro — garante consistência e evita inversão dia/mês."""
    if series is None or len(series) == 0:
        return pd.to_datetime(series, errors="coerce")
    ordem = detectar_ordem(series, default=default_order)
    limpa = series.map(_only_date_part)
    return limpa.map(lambda s: _parse_um(s, ordem))


def parse_date_single(value, ordem: str = "DMY") -> pd.Timestamp:
    """Parse de um único valor (quando não há coluna para detectar a ordem)."""
    return _parse_um(_only_date_part(value), ordem)
