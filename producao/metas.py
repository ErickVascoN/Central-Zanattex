"""
Carregador das metas de facções — guia de metas dentro da planilha de facções.
Portado da Central (utils/metas_manager.py::load_metas_from_faccoes_sheet),
adaptado ao Django. Fonte primária; se indisponível, retorna lista vazia
(sem os fallbacks legado/JSON/hardcoded da Central, que podem entrar depois).

Formatos suportados na guia:
  - Facção + Produto + Meta (sem cliente → vale para todos os clientes)
  - Metas por cliente em colunas extras no padrão "NOME NÚMERO" (ex: "BURDAYS 500")
  - Tabela secundária lateral (ex: ZANATTA): Facção + Produto + Meta + Cliente
"""

from __future__ import annotations

import io
import logging
import re

import pandas as pd

from integracao import db_reader
from integracao.sheets_client import get_raw
from integracao.date_parser import parse_date_series
from integracao.normalize import normalize_text, is_blank
from integracao.fontes import (
    FACCOES_SHEET_ID,
    FACCOES_GID_METAS,
    FACCOES_FACCAO_ALIAS,
    METAS_TTL,
)

logger = logging.getLogger(__name__)

# Baseline de dias úteis/mês para converter meta diária → mensal (consistente
# com os valores da Central).
_DIAS_UTEIS_MES = 22


def _col(df: pd.DataFrame, *substrings: str) -> str | None:
    for col in df.columns:
        col_n = normalize_text(col)
        if all(normalize_text(s) in col_n for s in substrings):
            return col
    return None


def _to_int(v) -> int:
    try:
        return int(float(str(v).replace(".", "").replace(",", ".").strip()))
    except Exception:
        return 0


def load_metas() -> list[dict]:
    """Lê a tabela sincronizada `producao_metas` (ver `producao/sync.py`) e
    devolve no formato de sempre: lista de
    {produto, cliente, faccao, meta_dia, meta_mes, meta_semana}. Vazio se a
    tabela ainda não foi sincronizada."""
    df = db_reader.ler_tabela("producao_metas")
    if df is None or df.empty:
        return []
    campos_int = ("meta_dia", "meta_mes", "meta_semana")
    return [
        {**{k: v for k, v in row.items() if k not in campos_int},
         **{k: int(row[k]) for k in campos_int}}
        for row in df.to_dict("records")
    ]


def load_metas_do_sheets() -> list[dict]:
    """Lê a guia de metas DIRETO do Google Sheets (loader original) — chamada
    só pelo sync (`producao/sync.py`), nunca por uma view. Cada item:
    {produto, cliente, faccao, meta_dia, meta_mes, meta_semana}. Vazio se indisponível."""
    csv_text = get_raw(FACCOES_SHEET_ID, FACCOES_GID_METAS, ttl=METAS_TTL)
    if not csv_text:
        return []
    try:
        df = pd.read_csv(io.StringIO(csv_text), dtype=str)
    except Exception as e:
        logger.warning("metas: erro ao parsear CSV: %s", e)
        return []

    cols_list = list(df.columns)
    c_faccao   = _col(df, "FACCAO") or _col(df, "RESPONSAVEL") or _col(df, "PRESTADOR")
    c_produto  = _col(df, "PRODUTO")
    c_meta_mes = _col(df, "META", "MES") or _col(df, "METAS") or _col(df, "META")
    if not all([c_faccao, c_produto, c_meta_mes]):
        logger.warning("metas: colunas obrigatórias ausentes. Disponíveis: %s", cols_list)
        return []

    # Tabela secundária lateral: 2ª ocorrência de FACÇÃO marca o início.
    c_fac2 = next((c for c in cols_list if normalize_text(c).startswith("FACCAO") and c != c_faccao), None)
    _sec_start = cols_list.index(c_fac2) if c_fac2 else len(cols_list)

    c_cliente = next((c for c in cols_list[:_sec_start] if normalize_text(c).startswith("CLIENTE")), None)

    fac_alias = {normalize_text(k): v.upper() for k, v in FACCOES_FACCAO_ALIAS.items()}

    def _apply_alias(name: str) -> str:
        return fac_alias.get(normalize_text(name), name)

    def _make_entry(faccao: str, produto: str, cliente: str, meta_dia: int) -> dict:
        return {
            "produto":     produto,
            "cliente":     cliente,
            "faccao":      _apply_alias(faccao.strip().upper()),
            "meta_dia":    meta_dia,
            "meta_mes":    int(meta_dia * _DIAS_UTEIS_MES),
            "meta_semana": int(meta_dia * 5),
        }

    _extra_cols = [
        c for c in cols_list[:_sec_start]
        if (c.startswith("Unnamed:") or re.match(r"METAS?\s*[2-9]", normalize_text(c)))
        and c != c_meta_mes
    ]
    _cols_extras_scan = _extra_cols + [c_meta_mes]

    df["_meta_mes"] = df[c_meta_mes].apply(_to_int)
    df["_tem_extras"] = False
    if _extra_cols:
        def _tem_extras_fn(row):
            if row["_meta_mes"] > 0:
                return False
            for c in _cols_extras_scan:
                v = str(row[c]).strip() if pd.notna(row[c]) else ""
                if re.search(r"\d", v):
                    return True
            return False
        df["_tem_extras"] = df.apply(_tem_extras_fn, axis=1)

    c_prod2 = next((c for c in cols_list[_sec_start:] if normalize_text(c).startswith("PRODUTO")), None)
    c_meta2 = next((c for c in cols_list[_sec_start:] if normalize_text(c).startswith("META")), None)
    c_cli2  = next((c for c in cols_list[_sec_start:] if normalize_text(c).startswith("CLIENTE")), None)

    rows: list[dict] = []

    # Tabela principal
    df_main = df[(df["_meta_mes"] > 0) | df["_tem_extras"]].copy()
    for _, row in df_main.iterrows():
        faccao_raw  = str(row[c_faccao]).strip().upper()
        produto_raw = str(row[c_produto]).strip().upper()
        cliente_raw = str(row[c_cliente]).strip().upper() if c_cliente else "__ALL__"
        if is_blank(faccao_raw) or is_blank(produto_raw):
            continue

        if row["_tem_extras"]:
            for ec in _cols_extras_scan:
                v = str(row[ec]).strip() if pd.notna(row[ec]) else ""
                m = re.match(r"^(.+?)\s+(\d[\d.,]*)$", v.strip())
                if not m:
                    continue
                cli_extra = m.group(1).strip().upper()
                meta_dia = _to_int(m.group(2))
                if meta_dia > 0 and not is_blank(cli_extra):
                    rows.append(_make_entry(faccao_raw, produto_raw, cli_extra, meta_dia))
        else:
            meta_dia = int(row["_meta_mes"])
            cliente = "" if (cliente_raw == "__ALL__" or is_blank(cliente_raw)) else cliente_raw
            rows.append(_make_entry(faccao_raw, produto_raw, cliente, meta_dia))

    # Tabela secundária (facção + produto + meta + cliente)
    if c_fac2 and c_prod2 and c_meta2:
        df_sec = df[df[c_fac2].notna() & (df[c_fac2].str.strip() != "")].copy()
        df_sec["_meta2"] = df_sec[c_meta2].apply(_to_int)
        df_sec = df_sec[df_sec["_meta2"] > 0]
        df_sec = df_sec.drop_duplicates(subset=[c_fac2, c_prod2, c_cli2, "_meta2"])
        for _, row in df_sec.iterrows():
            fac2  = str(row[c_fac2]).strip().upper()
            prod2 = str(row[c_prod2]).strip().upper() if pd.notna(row[c_prod2]) else ""
            cli2  = str(row[c_cli2]).strip().upper() if pd.notna(row[c_cli2]) else ""
            meta2 = int(row["_meta2"])
            if is_blank(fac2) or is_blank(prod2):
                continue
            rows.append(_make_entry(fac2, prod2, cli2, meta2))

    logger.info("metas: %d entradas carregadas.", len(rows))
    return rows
