"""
Carregador robusto do Corte · Lençol (Arealva) — planilha própria, separada das
Mantas. Portado de utils/lencol_loader_smart.py (Streamlit original): a coluna
DATA vem sempre certa por PADRÃO DE CONTEÚDO (nem sempre o cabeçalho da
primeira linha é confiável — pode ter título mesclado, ordem trocada etc.),
então detectamos a linha de cabeçalho real e mapeamos colunas pelo conteúdo.
"""

from __future__ import annotations

import io
import re

import pandas as pd

from integracao.sheets_client import get_raw
from integracao.date_parser import parse_date_series
from integracao.fontes import FONTES

_OP_HEADER_RE = re.compile(r"^N?[°º]?\s*OP\.?$")


def _is_op_header(v: str) -> bool:
    return bool(_OP_HEADER_RE.match(v))


def load_lencol_raw() -> pd.DataFrame:
    """DataFrame bruto (colunas: DATA, PRESTADOR, OP, CATEGORIA, EMPRESA,
    TECIDO, QUANT, VALOR_PECA, VALOR_RECEBER, RETALHO_KG, OBS). Vazio se
    indisponível ou a estrutura não bater com o esperado."""
    fonte = FONTES.get("corte_lencol")
    if not fonte:
        return pd.DataFrame()
    conteudo = get_raw(fonte["id"], fonte["gid"], ttl=fonte.get("ttl", 60))
    if not conteudo or not conteudo.strip():
        return pd.DataFrame()

    raw = pd.read_csv(io.StringIO(conteudo), header=None, dtype=str)
    hdr_idx = 0
    for i in range(min(20, len(raw))):
        vals = [str(v).strip().upper() for v in raw.iloc[i].tolist()]
        if any("PRESTADOR" in v for v in vals) and any(_is_op_header(v) for v in vals):
            hdr_idx = i
            break
    df = raw.iloc[hdr_idx + 1:].copy().reset_index(drop=True)
    df.columns = [
        str(c).strip() if (c is not None and str(c).strip() and str(c) != "nan")
        else f"COL_{i}"
        for i, c in enumerate(raw.iloc[hdr_idx].tolist())
    ]

    # ── Mapeia colunas por CONTEÚDO (o cabeçalho sozinho não é confiável) ────
    col_map: dict[str, str] = {}
    for i, col in enumerate(df.columns[:20]):
        col_upper = col.upper().strip()
        sample = df[col].dropna().head(10)

        is_date_col = False
        if not sample.empty:
            sample_vals = sample.astype(str).tolist()
            date_count = sum(
                1 for v in sample_vals
                if "/" in v and len(v.split("/")) == 3
                and any(part.isdigit() for part in v.split("/"))
                and ("202" in v or "203" in v)
            )
            is_date_col = date_count >= 1

        if is_date_col and "DATA" not in col_map:
            col_map["DATA"] = col
        elif col_upper in ("PRESTADOR/NOME", "PRESTADOR/NOME "):
            col_map["PRESTADOR"] = col
        elif _is_op_header(col_upper):
            col_map["OP"] = col
        elif col_upper in ("CATEGORIA", "CATEGORIA "):
            col_map["CATEGORIA"] = col
        elif (col_upper in ("EMPRESA", "EMPESA") or col_upper in ("DATA ", "DATA")) and "EMPRESA" not in col_map:
            col_map["EMPRESA"] = col
        elif col_upper in ("TECIDO/PRODUTO", "TECIDO/PRODUTO "):
            col_map["TECIDO"] = col
        elif col_upper == "QUANT":
            col_map["QUANT"] = col
        elif "R$ A RECEBER" in col_upper:
            col_map["VALOR_RECEBER"] = col
        elif "R$ PEÇA" in col_upper and "VALOR_PECA" not in col_map:
            col_map["VALOR_PECA"] = col
        elif "RETALHO" in col_upper:
            col_map["RETALHO_KG"] = col
        elif "OBS" in col_upper or "OBSERVAÇÕES" in col_upper:
            col_map["OBS"] = col

    df_clean = pd.DataFrame()
    for col_novo, col_orig in col_map.items():
        if col_orig in df.columns:
            df_clean[col_novo] = df[col_orig]
    if df_clean.empty or "DATA" not in df_clean.columns:
        return pd.DataFrame()
    df = df_clean

    df = df[(df.notna().sum(axis=1) > 1)].copy().reset_index(drop=True)
    if df.empty:
        return pd.DataFrame()

    df["DATA"] = parse_date_series(df["DATA"])
    df = df[df["DATA"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    for col in ("QUANT", "VALOR_PECA", "VALOR_RECEBER", "RETALHO_KG"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace("R$", "", regex=False).str.strip()
                .str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
                errors="coerce",
            ).fillna(0)
    if "QUANT" in df.columns:
        df["QUANT"] = df["QUANT"].astype(int)

    if "OP" in df.columns:
        invalidos_op = {"", "NAN", "NONE", "N/A", "NAT", "-"}
        df["OP"] = df["OP"].astype(str).str.strip()
        df.loc[df["OP"].str.upper().isin(invalidos_op), "OP"] = "SEM OP"

    tem_quant = df["QUANT"] > 0 if "QUANT" in df.columns else False
    tem_prest = (
        df["PRESTADOR"].astype(str).str.strip().str.upper()
        .replace({"NAN": "", "NONE": "", "N/A": ""}).ne("")
        if "PRESTADOR" in df.columns else False
    )
    tem_cat = (
        df["CATEGORIA"].astype(str).str.strip().str.upper()
        .replace({"NAN": "", "NONE": "", "N/A": ""}).ne("")
        if "CATEGORIA" in df.columns else False
    )
    df = df[tem_quant | tem_prest | tem_cat].copy().reset_index(drop=True)
    return df
