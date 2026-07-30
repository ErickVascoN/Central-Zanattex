"""
Carregador das planilhas de produção dos COLABORADORES INTERNOS.
Portado da Central (utils/producao_interno_loader.py), adaptado ao Django.

4 unidades (config em integracao.fontes.PRODUCAO_INTERNO):
    LITTEX, GGTTEX_JOGOS, GGTTEX_FRONHA, GGTTEX_CORTINA.

Particularidades (motivo do loader dedicado):
  - Título mesclado embutido no nome de colunas → detecção por SUBSTRING.
  - Datas em M/D/YYYY (locale US) → parse_date_series com date_order da config.
  - Quantidade com ponto de milhar → limpeza numérica.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from integracao.sheets_client import get_raw
from integracao.date_parser import parse_date_series
from integracao.normalize import normalize_text
from integracao.fontes import PRODUCAO_INTERNO, TTL_PADRAO

logger = logging.getLogger(__name__)

_TEXTO_VAZIO = {"", "NAN", "NONE", "N/A", "NA", "NAT", "-", "--"}

# GGTTEX_JOGOS: tamanhos reconhecidos na coluna DESCRIÇÃO (costureiras)
_TAMANHOS_JOGO_NORM: dict[str, str] = {
    "CASAL": "CASAL", "CS": "CASAL",
    "SOLTEIRO": "SOLTEIRO", "ST": "SOLTEIRO", "SOL": "SOLTEIRO",
    "QUEEN": "QUEEN", "QE": "QUEEN",
    "KING": "KING", "KG": "KING",
}


def _achar_coluna(colunas, *termos) -> str | None:
    """Nome original da 1ª coluna cujo nome normalizado contém algum dos termos."""
    termos_norm = [normalize_text(t) for t in termos]
    for col in colunas:
        cn = normalize_text(col)
        if any(t and t in cn for t in termos_norm):
            return col
    return None


def _limpar_texto(serie: pd.Series) -> pd.Series:
    s = serie.fillna("").astype(str).str.strip()
    return s.where(~s.str.upper().isin(_TEXTO_VAZIO), "")


def _limpar_qtd(serie: pd.Series) -> pd.Series:
    """'1.186' → 1186 ; '1.234,5' → 1234 ; vazio → 0. Inteiro."""
    s = (
        serie.astype(str)
        .str.replace("R$", "", regex=False)
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(s, errors="coerce").fillna(0).astype(int)


def load_interno_unidade(chave: str) -> pd.DataFrame:
    """Carrega e normaliza uma unidade interna. Colunas: DATA, COLABORADOR,
    SETOR, FUNCAO, OBSERVACAO, PRODUTO, CLIENTE, QUANTIDADE (+ TAMANHO em JOGOS).
    Vazio em caso de erro."""
    cfg = PRODUCAO_INTERNO.get(chave)
    if not cfg:
        logger.error("Unidade interna desconhecida: %s", chave)
        return pd.DataFrame()

    try:
        conteudo = get_raw(cfg["id"], cfg["gid"], ttl=TTL_PADRAO)
        if not conteudo or not conteudo.strip():
            logger.error("%s: CSV vazio/indisponível", chave)
            return pd.DataFrame()

        raw = pd.read_csv(io.StringIO(conteudo), header=0, dtype=str)
        raw.columns = [str(c).strip() for c in raw.columns]
        cols = list(raw.columns)

        col_data  = _achar_coluna(cols, "DATA")
        col_colab = _achar_coluna(cols, "PRESTADOR", "COLABORADOR")
        col_qtd   = _achar_coluna(cols, "TOTAL PRODUZIDO", "TOTAL CONFERIDO", "TOTAL")
        col_setor = _achar_coluna(cols, "SETOR")
        col_func  = _achar_coluna(cols, "FUNCAO")
        col_obs   = _achar_coluna(cols, "OBSERVA")
        if chave == "LITTEX":
            col_prod = _achar_coluna(cols, "PRODUTO") or _achar_coluna(cols, "DESCRICAO")
        else:
            col_prod = _achar_coluna(cols, "DESCRICAO") or _achar_coluna(cols, "PRODUTO")
        col_cli = _achar_coluna(cols, "CLIENTE") or _achar_coluna(cols, "EMPRESA")

        if not col_colab or not col_qtd or not col_data:
            logger.error("%s: colunas essenciais ausentes (colab=%s qtd=%s data=%s). Disp: %s",
                         chave, col_colab, col_qtd, col_data, cols)
            return pd.DataFrame()

        out = pd.DataFrame()
        out["DATA"] = raw[col_data]
        out["COLABORADOR"] = (
            raw[col_colab].fillna("").astype(str)
            .str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
        )
        out["QUANTIDADE"] = _limpar_qtd(raw[col_qtd])
        out["SETOR"]      = _limpar_texto(raw[col_setor]) if col_setor else ""
        out["FUNCAO"]     = _limpar_texto(raw[col_func]) if col_func else ""
        out["OBSERVACAO"] = _limpar_texto(raw[col_obs]) if col_obs else ""
        out["PRODUTO"]    = _limpar_texto(raw[col_prod]) if col_prod else ""
        out["CLIENTE"]    = _limpar_texto(raw[col_cli]) if col_cli else ""

        _date_order = cfg.get("date_order", "DMY")
        out["DATA"] = parse_date_series(out["DATA"], default_order=_date_order)

        # Não filtra por QUANTIDADE > 0 aqui: linhas com 0 e uma OBSERVACAO
        # (falta, revisão de carga, sábado etc.) são registros legítimos —
        # descartá-las aqui as escondia de tudo, inclusive do "ritmo diário"
        # do relatório. Quem precisa só de dias com produção real (dias
        # ativos, ranking, assiduidade) já filtra QUANTIDADE > 0 explicitamente
        # (ver producao/servicos.py:consistencia, interno_servicos.py etc.).
        vazios = {"", "NAN", "NONE", "N/A", "-"}
        tem_colab = ~out["COLABORADOR"].str.upper().isin(vazios)
        out = out[tem_colab & out["DATA"].notna()].reset_index(drop=True)

        # Descarta datas de ano atípico (erros de digitação distorcem o filtro)
        if not out.empty and out["DATA"].notna().any():
            _anos = out["DATA"].dt.year
            _ano_dom = int(_anos.mode().iloc[0])
            _bad = _anos != _ano_dom
            if _bad.any():
                out = out[~_bad].reset_index(drop=True)

        # GGTTEX_JOGOS: extrai FUNCAO (tipo de costura / atividade) e TAMANHO
        if chave == "GGTTEX_JOGOS" and "SETOR" in out.columns and "PRODUTO" in out.columns:
            _setor_u = out["SETOR"].astype(str).str.strip().str.upper()
            _descr_u = out["PRODUTO"].astype(str).str.strip().str.upper()
            _is_costura = _setor_u.str.startswith("COSTURA")
            out["FUNCAO"] = _setor_u.where(_is_costura, _descr_u).fillna("")
            out["TAMANHO"] = (
                _descr_u.map(lambda v: _TAMANHOS_JOGO_NORM.get(v, ""))
                .where(_is_costura, "").fillna("")
            )

        logger.info("%s: %d linhas válidas", chave, len(out))
        return out

    except Exception as e:
        logger.error("%s: erro %s", chave, str(e)[:150])
        return pd.DataFrame()
