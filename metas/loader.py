"""
Carregador da aba "BD PLANO DE METAS" (mesma planilha de Produção de Facções,
ver `integracao.fontes.FONTES["plano_metas"]`) — base do dashboard Plano de
Metas. Uma linha por (Cliente, Responsável, Produto, Mês, Status), com
Status em {PREVISTO, REALIZADO}.

Não existe página Streamlit original disponível neste repositório pra portar
bug a bug (projeto separado, fora do alcance) — parser escrito a partir da
inspeção direta do CSV real desta aba.
"""

from __future__ import annotations

import io
import re

import pandas as pd

from integracao.sheets_client import get_raw_sheet
from integracao.fontes import FONTES

_MESES_ABREV = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

_DATA_BASE_RE = re.compile(r"^\s*(\d{1,2})-([a-zç]{3})\.?\s*$", re.IGNORECASE)

_COL_MAP = {
    "CLIENTE": "CLIENTE", "RESPONSÁVEL": "RESPONSAVEL",
    "ATIVIDADE": "ATIVIDADE", "TIPO": "TIPO",
    "CENTRO DE CUSTO": "CENTRO_CUSTO", "PRODUTO": "PRODUTO",
    "META MÊS": "QUANTIDADE", "PRODUÇÃO DIÁRIA": "PRODUCAO_DIARIA",
    "VLR UNITARIO": "VLR_UNITARIO", "CUSTO": "CUSTO",
    "PREÇO FINAL": "PRECO_FINAL", "CUSTO FINAL": "CUSTO_FINAL",
    "DATA BASE": "DATA_BASE_RAW", "STATUS": "STATUS", "FONTE": "FONTE",
    "DATA FIM": "DATA_FIM_RAW",
}

_COLS_TEXTO = ("CLIENTE", "RESPONSAVEL", "ATIVIDADE", "TIPO", "CENTRO_CUSTO",
               "PRODUTO", "STATUS", "FONTE")
_COLS_VALOR = ("QUANTIDADE", "PRODUCAO_DIARIA", "VLR_UNITARIO", "CUSTO",
               "PRECO_FINAL", "CUSTO_FINAL")


def _parse_data_base(valor, hoje: pd.Timestamp) -> pd.Timestamp | None:
    """'1-fev.' -> Timestamp do dia 1 daquele mês. A planilha não tem ano —
    assume o ano corrente, e o anterior se o mês cair mais de 1 mês no
    futuro (evita virar o ano errado perto da virada dez/jan)."""
    m = _DATA_BASE_RE.match(str(valor).strip())
    if not m:
        return None
    dia, mes_abrev = int(m.group(1)), m.group(2).lower()
    mes = _MESES_ABREV.get(mes_abrev)
    if not mes:
        return None
    ano = hoje.year
    try:
        candidato = pd.Timestamp(year=ano, month=mes, day=dia)
    except ValueError:
        return None
    if candidato > hoje + pd.DateOffset(months=1):
        candidato = pd.Timestamp(year=ano - 1, month=mes, day=dia)
    return candidato


def _parse_valor(serie: pd.Series) -> pd.Series:
    """'R$ 0,44' / '  3.520,00 ' / '  -   ' -> float (pt-BR, milhar+decimal)."""
    s = (
        serie.astype(str)
        .str.replace("R$", "", regex=False)
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _parse_serial_excel(serie: pd.Series) -> pd.Series:
    """DATA FIM sai como número serial do Excel/Sheets (dias desde
    1899-12-30) em vez de data formatada, no formato pt-BR ("46.082,00") —
    converte quando for numérico, NaT quando não for (não usada em nenhum
    indicador, só guardada)."""
    numeros = pd.to_numeric(_parse_valor(serie), errors="coerce")
    return pd.to_datetime(numeros, unit="D", origin="1899-12-30")


def load_plano_metas_do_sheets() -> pd.DataFrame:
    """DataFrame limpo: CLIENTE, RESPONSAVEL, ATIVIDADE, TIPO, CENTRO_CUSTO,
    PRODUTO, QUANTIDADE, PRODUCAO_DIARIA, VLR_UNITARIO, CUSTO, PRECO_FINAL,
    CUSTO_FINAL, MES, STATUS (PREVISTO/REALIZADO), FONTE, DATA_FIM. Direto do
    Google Sheets (loader original) — chamado só pelo sync (`metas/sync.py`),
    nunca por uma view. Vazio se indisponível ou faltarem colunas essenciais."""
    fonte = FONTES.get("plano_metas")
    if not fonte:
        return pd.DataFrame()
    conteudo = get_raw_sheet(fonte["id"], "BD PLANO DE METAS", ttl=fonte.get("ttl", 300))
    if not conteudo or not conteudo.strip():
        return pd.DataFrame()

    try:
        raw = pd.read_csv(io.StringIO(conteudo), header=0, dtype=str)
    except Exception:
        return pd.DataFrame()
    raw.columns = [str(c).strip() for c in raw.columns]

    if any(c not in raw.columns for c in _COL_MAP):
        return pd.DataFrame()

    out = pd.DataFrame({novo: raw[original] for original, novo in _COL_MAP.items()})

    for col in _COLS_TEXTO:
        out[col] = out[col].fillna("").astype(str).str.strip().str.upper()

    for col in _COLS_VALOR:
        out[col] = _parse_valor(out[col])
    out["QUANTIDADE"] = out["QUANTIDADE"].astype(int)

    hoje = pd.Timestamp.now().normalize()
    out["MES"] = out["DATA_BASE_RAW"].apply(lambda v: _parse_data_base(v, hoje))
    out["DATA_FIM"] = _parse_serial_excel(out["DATA_FIM_RAW"])
    out = out.drop(columns=["DATA_BASE_RAW", "DATA_FIM_RAW"])

    out = out[out["MES"].notna() & out["STATUS"].isin(["PREVISTO", "REALIZADO"])]
    return out.reset_index(drop=True)
