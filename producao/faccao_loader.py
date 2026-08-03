"""
Carregador da produção externa (facções / prestadores) — fonte VIVA da produção
diária atual (a partir do cutover 01/06/2026).

Portado da Central (utils/faccao_loader.py), adaptado ao Django: usa
integracao.sheets_client / date_parser / normalize e a config de
integracao.fontes. Removida a gravação em SQLite (db_manager) — o espelho no
banco será feito à parte quando necessário.

Planilha única com múltiplas abas — uma por facção. Estrutura de cada aba:
DATA, PRODUTO, CLIENTE, QUANTIDADE (+ PRESTADOR na aba QUARTERIZADAS).
Colunas depois da primeira vazia são tabelas de referência lateral e ignoradas.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from integracao.sheets_client import get_raw, get_raw_sheet
from integracao.date_parser import parse_date_series
from integracao.normalize import normalize_text
from integracao.fontes import (
    FACCOES_SHEET_ID,
    FACCOES_TTL,
    FACCOES_ABAS,
    FACCOES_PRODUTO_ALIAS,
    FACCOES_CLIENTE_ALIAS,
)

logger = logging.getLogger(__name__)

_BLANKS = frozenset({"", "NAN", "NONE", "N/A", "NA", "NAT", "-", "--", "DD/MM/AAAA"})

# Etapas do processo que algumas facções detalham além do total em
# QUANTIDADE (hoje só a GGTTEX Rute, a partir de 03/08/2026) — cada item é
# (coluna no DataFrame/banco, rótulo de exibição, termos de busca no
# cabeçalho da planilha). Pra adicionar uma etapa nova (ex.: mais uma peça do
# processo) só precisa mexer aqui — loader, dashboard e PDF pegam a lista
# inteira sozinhos, sem precisar reajustar layout na mão.
ETAPAS_PROCESSO: list[tuple[str, str, tuple[str, ...]]] = [
    ("CANTO", "Canto", ("CANTO",)),
    ("LENCOL_CIMA", "Lençol de Cima", ("LENCOL DE CIMA", "LENÇOL DE CIMA")),
    ("ELASTICADO", "Elasticado", ("ELASTICADO",)),
    ("FRONHA", "Fronha", ("FRONHA",)),
]


def _find_col(cols: list[str], *terms: str) -> str | None:
    """Retorna a 1ª coluna cujo nome normalizado contém algum dos termos."""
    for col in cols:
        cn = normalize_text(col)
        if any(normalize_text(t) in cn for t in terms if t):
            return col
    return None


def _parse_qty(series: pd.Series) -> pd.Series:
    """'1.234' → 1234; '1.234,5' → 1234; vazio → 0. Retorna inteiro."""
    s = (
        series.astype(str)
        .str.strip()
        .str.replace(r"[R$\s]", "", regex=True)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(s, errors="coerce").fillna(0).astype(int)


def _load_tab(sheet_id: str, tab_name: str, cfg: dict, ttl: int) -> pd.DataFrame | None:
    """Carrega uma aba individual — por GID quando definido, ou por NOME
    (aba nova, sem gid fixo ainda) — e retorna DataFrame normalizado."""
    effective_sheet_id = cfg.get("sheet_id", sheet_id)
    if cfg.get("gid"):
        content = get_raw(effective_sheet_id, cfg["gid"], ttl=ttl)
    else:
        content = get_raw_sheet(effective_sheet_id, tab_name, ttl=ttl)
    if not content or not content.strip():
        logger.warning("Tab %r (gid=%s): sem dados / indisponível", tab_name, cfg.get("gid"))
        return None

    raw = pd.read_csv(io.StringIO(content), dtype=str, header=0)
    raw.columns = [str(c).strip() for c in raw.columns]

    # Trunca na primeira coluna sem nome (separa dados da tabela de referência lateral)
    cols = list(raw.columns)
    stop = next((i for i, c in enumerate(cols) if c.startswith("Unnamed:")), len(cols))
    raw = raw.iloc[:, :stop]
    cols = list(raw.columns)

    col_map_cfg = cfg.get("col_map", {})

    def _col(field: str, *defaults: str) -> str | None:
        if field in col_map_cfg:
            return _find_col(cols, col_map_cfg[field])
        return _find_col(cols, *defaults)

    col_data  = _col("data",       "DATA")
    col_prod  = _col("produto",    "PRODUTO")
    col_cli   = _col("cliente",    "CLIENTE")
    col_qtd   = _col("quantidade", "QUANTIDADE")
    col_prest = _col("prestador",  "PRESTADOR")
    col_obs   = _col("observacao", "OBSERVACAO", "OBS")
    # Detalhamento por etapa do processo — colunas opcionais, ausentes na
    # maioria das abas (ver ETAPAS_PROCESSO no topo do arquivo).
    cols_etapa = {col_db: _col(col_db.lower(), *termos) for col_db, _label, termos in ETAPAS_PROCESSO}

    if not all([col_data, col_prod, col_cli, col_qtd]):
        logger.warning("Tab %r: colunas essenciais não encontradas. Disponíveis: %s", tab_name, cols)
        return None

    # fillna ANTES do astype(str): no pandas 3.x, astype(str) não converte NaN
    # real em "nan"; o valor permanece float e escapa dos filtros de vazios.
    out = pd.DataFrame()
    out["DATA"]       = raw[col_data]
    out["PRODUTO"]    = raw[col_prod].fillna("").astype(str).str.strip().str.upper()
    out["CLIENTE"]    = raw[col_cli].fillna("").astype(str).str.strip().str.upper()
    out["QUANTIDADE"] = _parse_qty(raw[col_qtd])
    out["ABA"]        = tab_name.strip()
    out["PRESTADOR"]  = (
        raw[col_prest].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
        if col_prest else ""
    )
    out["OBSERVACAO"] = (
        raw[col_obs].fillna("").astype(str).str.strip() if col_obs else ""
    )
    for col_db, col_planilha in cols_etapa.items():
        out[col_db] = _parse_qty(raw[col_planilha]) if col_planilha else 0

    # Facção: nome fixo da config OU o próprio prestador (abas quarterizadas).
    if cfg.get("por_prestador"):
        out["FACCAO"] = out["PRESTADOR"].where(
            out["PRESTADOR"].str.strip() != "", "QUARTERIZADA (S/ NOME)"
        )
    else:
        out["FACCAO"] = cfg["faccao"]

    # Datas: gviz exporta no formato M/D/YYYY (locale US do Google)
    out["DATA"] = parse_date_series(out["DATA"], default_order="MDY")

    # Corrige datas que caíram no futuro por mistura de formatos na mesma coluna
    # (linhas digitadas à mão em DD/MM/YYYY dentro de uma coluna majoritariamente
    # M/D/YYYY). Produção real nunca é no futuro: se a data ficou após hoje e
    # dia/mês são ambos ≤12, tenta a versão invertida.
    _hoje = pd.Timestamp.now().normalize()

    def _corrigir_data_futura(ts):
        if pd.isna(ts) or ts <= _hoje:
            return ts
        d, m = ts.day, ts.month
        if d <= 12 and m <= 12:
            try:
                alt = pd.Timestamp(year=ts.year, month=d, day=m)
                if alt <= _hoje:
                    return alt
            except ValueError:
                pass
        return ts

    out["DATA"] = out["DATA"].apply(_corrigir_data_futura)

    # Remove linhas inválidas. Exceções que mantêm a linha mesmo com
    # QUANTIDADE=0: Observação preenchida (dia sem produção, só pra
    # contextualização — não entra em somas nem conta como dia de produção
    # nas médias) OU alguma etapa do processo preenchida (ver ETAPAS_PROCESSO).
    raw_data_upper = raw[col_data].fillna("").astype(str).str.strip().str.upper()
    tem_obs = out["OBSERVACAO"].str.strip() != ""
    tem_etapa = pd.Series(False, index=out.index)
    for col_db, _label, _termos in ETAPAS_PROCESSO:
        tem_etapa = tem_etapa | (out[col_db] > 0)
    valid = (
        ~raw_data_upper.isin(_BLANKS)
        & out["DATA"].notna()
        & ((out["QUANTIDADE"] > 0) | tem_obs | tem_etapa)
        & ~out["PRODUTO"].str.upper().isin(_BLANKS)
        & ~out["CLIENTE"].str.upper().isin(_BLANKS)
    )
    out = out[valid].reset_index(drop=True)

    _fac_log = "POR PRESTADOR" if cfg.get("por_prestador") else cfg["faccao"]
    logger.info("Tab %r → %s: %d linhas", tab_name.strip(), _fac_log, len(out))
    return out


def load_faccoes() -> pd.DataFrame:
    """Carrega e unifica todas as abas da planilha de facções externas.

    Retorna DataFrame com colunas: DATA, FACCAO, ABA, PRESTADOR, PRODUTO,
    CLIENTE, QUANTIDADE, OBSERVACAO. Vazio em caso de erro/indisponibilidade.
    """
    dfs = []
    for tab_name, cfg in FACCOES_ABAS.items():
        df = _load_tab(FACCOES_SHEET_ID, tab_name, cfg, FACCOES_TTL)
        if df is not None and not df.empty:
            dfs.append(df)

    if not dfs:
        logger.error("load_faccoes: nenhuma aba retornou dados")
        return pd.DataFrame()

    result = pd.concat(dfs, ignore_index=True)

    # Remove linhas onde PRODUTO == FACCAO (artefato de cabeçalho/grupo na aba
    # QUARTERIZADAS onde o nome do prestador aparece como "produto").
    result = result[
        result["PRODUTO"].str.upper() != result["FACCAO"].str.upper()
    ].reset_index(drop=True)

    # Alias de produtos (OUTLET PRENSADO → MANTA PRENSADA, FRONHA → FRONHA AVULSA…)
    prod_alias = {normalize_text(k): v for k, v in FACCOES_PRODUTO_ALIAS.items()}
    result["PRODUTO"] = result["PRODUTO"].apply(lambda p: prod_alias.get(normalize_text(p), p))

    # Alias de clientes: exato (NC INDUSTRIA → NIAZITTEX) e qualquer "NIAZI…" → NIAZITTEX.
    cli_alias = {normalize_text(k): v for k, v in FACCOES_CLIENTE_ALIAS.items()}

    def _canon_cliente(c: str) -> str:
        cn = normalize_text(c)
        if cn in cli_alias:
            return cli_alias[cn]
        if "NIAZI" in cn:
            return "NIAZITTEX"
        return c

    result["CLIENTE"] = result["CLIENTE"].apply(_canon_cliente)
    return result
