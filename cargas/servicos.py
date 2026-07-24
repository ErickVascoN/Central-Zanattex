"""
Serviço de dados do dashboard de Previsão de Cargas (previsão x realizado).

Portado de utils/cargas_loader.py (Streamlit) linha a linha — a planilha tem
uma aba por mês, sem layout fixo de colunas (célula mesclada, painel diário de
realizado em posição variável, linha de resumo mensal em formatos diferentes
por mês). Toda a heurística de parsing e as correções de bugs feitas no
original foram preservadas; só a origem dos dados mudou (get_raw_sheet do
Django em vez de urllib) e a persistência em banco (Histórico) foi removida —
ainda não portada nesta fase.
"""

from __future__ import annotations

import calendar
import csv
import io
import re
import unicodedata
from collections import Counter
from datetime import date

import pandas as pd

from integracao.fontes import CARGAS_SHEET_ID, CARGAS_TTL
from integracao.sheets_client import get_raw_sheet

MESES_NOMES_PT = {
    1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL", 5: "MAIO", 6: "JUNHO",
    7: "JULHO", 8: "AGOSTO", 9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO",
}
MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

CORES = {
    "previsao": "#4ECDC4", "realizado": "#45B7D1",
    "diferenca_pos": "#48BB78", "diferenca_neg": "#FC8181",
    "accent": "#FFA726", "neutro": "#718096",
}


def _meses_disponiveis(ano_inicio: int = 2026, mes_inicio: int = 1) -> list[tuple[str, int, int]]:
    """(NOME, mes, ano) de ano_inicio/mes_inicio até o mês atual."""
    hoje = date.today()
    meses = []
    ano, mes = ano_inicio, mes_inicio
    while (ano, mes) <= (hoje.year, hoje.month):
        meses.append((MESES_NOMES_PT[mes], mes, ano))
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return meses


MESES_DISPONIVEIS = _meses_disponiveis()

# Override manual do REALIZADO mensal para meses fechados com lançamento diário
# incompleto e não recuperável (confirmado com o usuário) — vem do relatório
# "Acompanhamento Mensal" (fechamento por empresa), fonte confiável nesses casos.
REALIZADO_MENSAL_OVERRIDE: dict[tuple[int, int], float] = {
    (2026, 5): 4_065_134.69,  # Maio/2026
}


# ── Helpers de parsing ────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return (unicodedata.normalize("NFD", str(s))
            .encode("ascii", "ignore").decode().upper().strip())


def _parse_money(s: str) -> float | None:
    s = str(s).strip()
    if not s or "R$" not in s:
        return None
    neg = s.startswith("-")
    clean = re.sub(r"[R$\s\-]", "", s).replace(".", "").replace(",", ".")
    try:
        v = float(clean)
        return -v if neg else v
    except ValueError:
        return None


def _parse_date_pt(s: str) -> date | None:
    raw = str(s).strip()
    sl = raw.lower()

    m = re.search(r"(\w+),\s+(\w+)\s+(\d+),\s+(\d{4})", sl)
    if m:
        month = MESES_PT.get(m.group(2))
        if month:
            try:
                return date(int(m.group(4)), month, int(m.group(3)))
            except ValueError:
                pass

    m2 = re.match(r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$", raw.strip())
    if m2:
        a, b, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            if a > 12:
                return date(y, b, a)
            elif b > 12:
                return date(y, a, b)
            else:
                return date(y, b, a)
        except ValueError:
            pass

    m3 = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$", raw.strip())
    if m3:
        try:
            return date(int(m3.group(1)), int(m3.group(2)), int(m3.group(3)))
        except ValueError:
            pass

    return None


def _is_date_str(s: str) -> bool:
    return bool(re.search(r"\d{4}", s)) and any(mes in s.lower() for mes in MESES_PT)


def _estimativa_mes_atual(df_raw: pd.DataFrame) -> dict | None:
    """Projeta o fechamento (Previsto e Realizado) do mês corrente.

    1. Previsto Projetado = Previsto lançado / dias cobertos pelos lançamentos
       x dias totais do mês (run-rate por dias com carga já lançada, não
       "dias corridos do calendário" — a planilha é preenchida em blocos
       semanais).
    2. Aderência Média = média simples de (Realizado/Previsto) dos 2 últimos
       meses fechados (Realizado oficial > 0) antes do mês corrente.
    3. Realizado Estimado = Previsto Projetado x Aderência Média.
    """
    hoje = date.today()
    ano_atual, mes_atual = hoje.year, hoje.month

    df_mes_atual = df_raw[(df_raw["ANO"] == ano_atual) & (df_raw["MES_NUM"] == mes_atual)]
    if df_mes_atual.empty:
        return None

    previsto_lancado = df_mes_atual["PREVISAO"].sum()
    dias_totais = calendar.monthrange(ano_atual, mes_atual)[1]

    df_cargas_mes = df_mes_atual[~df_mes_atual["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO"])]
    if df_cargas_mes.empty:
        return None
    dias_cobertos = (df_cargas_mes["DATA"].max() - df_cargas_mes["DATA"].min()).days + 1
    if dias_cobertos <= 0 or previsto_lancado <= 0:
        return None
    previsto_projetado = previsto_lancado / dias_cobertos * dias_totais

    df_real_mensal = (
        df_raw[df_raw["STATUS"] == "CARGO_REAL"]
        .groupby(["ANO", "MES_NUM"])
        .agg(PREVISAO=("PREVISAO", "sum"), REALIZADO=("REALIZADO", "sum"))
        .reset_index()
    )
    df_real_mensal = df_real_mensal[
        (df_real_mensal["REALIZADO"] > 0) & (df_real_mensal["PREVISAO"] > 0) &
        ((df_real_mensal["ANO"] < ano_atual) |
         ((df_real_mensal["ANO"] == ano_atual) & (df_real_mensal["MES_NUM"] < mes_atual)))
    ].sort_values(["ANO", "MES_NUM"])

    ultimos_2 = df_real_mensal.tail(2).copy()
    if ultimos_2.empty:
        return None
    ultimos_2["ADERENCIA"] = ultimos_2["REALIZADO"] / ultimos_2["PREVISAO"]
    aderencia_media = ultimos_2["ADERENCIA"].mean()
    realizado_estimado = previsto_projetado * aderencia_media

    return {
        "previsto_lancado": previsto_lancado,
        "previsto_projetado": previsto_projetado,
        "aderencia_media": aderencia_media,
        "aderencia_media_pct": aderencia_media * 100,
        "realizado_estimado": realizado_estimado,
        "dias_corridos": dias_cobertos,
        "dias_totais": dias_totais,
        "n_meses_base": len(ultimos_2),
    }


# ── Loader ────────────────────────────────────────────────────────────────────
def _fetch_csv(sheet_name: str) -> list[list[str]]:
    raw = get_raw_sheet(CARGAS_SHEET_ID, sheet_name, ttl=CARGAS_TTL)
    if not raw:
        return []
    return list(csv.reader(io.StringIO(raw)))


def _first_frete(row: list[str], limit: int | None = None) -> float:
    """Primeiro R$ positivo em cols 5 até `limit` (exclusivo, default col 10).

    `limit` deve ser a coluna-base do painel diário (_find_painel_col) quando
    conhecida — sem isso, cargas com célula de frete vazia (Armazenagem, frete
    subcontratado) podem herdar o Previsto de outro cliente no painel diário.
    """
    hi = min(limit, len(row)) if limit is not None else min(10, len(row))
    for j in range(5, hi):
        v = _parse_money(row[j])
        if v and abs(v) > 0:
            return abs(v)
    return 0.0


def _parse_num(s: str) -> float | None:
    """Parse numérico permissivo — aceita valor com ou sem prefixo R$."""
    s = str(s).strip()
    if not s or s in ("-", "R$", "R$  -", "R$-"):
        return None
    clean = re.sub(r"[R$\s\-]", "", s).replace(".", "").replace(",", ".")
    try:
        v = float(clean)
        return v if v > 0 else None
    except ValueError:
        return None


def _find_resumo_periodo_fim(label: str) -> tuple[int, int] | None:
    """Extrai (dia, mês) do fim do período coberto por um rótulo tipo
    "Total geral (01 a 11/07)"."""
    m = re.search(r"\(\s*\d{1,2}\s*a\s*(\d{1,2})/(\d{1,2})\s*\)", label)
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _find_resumo_mensal(rows: list[list[str]]) -> tuple[float, float, tuple[int, int] | None]:
    """Retorna (previsto_mensal, realizado_mensal, fim_periodo) a partir da
    linha de resumo da planilha.

    Estratégia:
    1. Cabeçalho "Previsto total" no painel direito → dois primeiros valores
       > R$1,5M na mesma linha (previsto, realizado).
    2. Linha com 'GERAL' em cols 8-14 → col[GERAL+2] é o realizado; col[GERAL+1]
       é o previsto quando presente.
    3. Maior valor não-redondo (not % 1.000) > R$1M em cols 8-15 de linhas
       não-data → realizado oficial do mês.
    """
    for row in rows:
        if _parse_date_pt(row[1] if len(row) > 1 else ""):
            continue
        big: list[float] = []
        for cell in row:
            v = _parse_num(str(cell))
            if v and v > 1_500_000:
                big.append(v)
        if len(big) >= 2:
            return (big[0], big[1], None)

    for row in rows:
        if _parse_date_pt(row[1] if len(row) > 1 else ""):
            continue
        for j in range(8, min(15, len(row))):
            if "GERAL" in _norm(row[j]):
                real_col = j + 2
                prev_col = j + 1
                if len(row) > real_col:
                    v = _parse_money(row[real_col])
                    if v and abs(v) > 1_000_000:
                        prev_v = _parse_money(row[prev_col]) if len(row) > prev_col else None
                        fim_periodo = _find_resumo_periodo_fim(row[j])
                        return (abs(prev_v) if prev_v else 0.0, abs(v), fim_periodo)

    best = 0.0
    for row in rows:
        if _parse_date_pt(row[1] if len(row) > 1 else ""):
            continue
        for j in range(8, min(16, len(row))):
            v = _parse_money(row[j])
            if not v:
                continue
            av = abs(v)
            if av <= 1_000_000:
                continue
            if round(av) % 1_000 == 0:
                continue
            if av > best:
                best = av
    if best > 0:
        return (0.0, best, None)

    return (0.0, 0.0, None)


def _find_painel_col(rows: list[list[str]]) -> int | None:
    """Detecta a coluna do painel diário de realizado ("DD-mmm.") — a posição
    muda de mês para mês; usa a coluna mais frequente entre as células que
    casam com o padrão de cabeçalho de dia."""
    contagem: Counter = Counter()
    for row in rows:
        for j, cell in enumerate(row):
            if re.match(r'^\s*\d{1,2}\s*[-\.]\s*[a-z]{3}', str(cell).strip().lower()):
                contagem[j] += 1
    if not contagem:
        return None
    return contagem.most_common(1)[0][0]


def _find_painel_col_rotulo(rows: list[list[str]]) -> int | None:
    """Fallback para meses sem cabeçalho 'DD-mmm.' repetido (ex.: Janeiro):
    localiza a coluna pelo rótulo de cabeçalho "REALIZADO" na 1ª linha."""
    if not rows:
        return None
    for j, cell in enumerate(rows[0]):
        if _norm(cell) == "REALIZADO" and j >= 2:
            return j - 2
    return None


def _extract_day_realized(rows: list[list[str]], mes_num: int, ano: int) -> dict:
    """Lê o painel direito do CSV e retorna {(data, cliente_norm): realizado}."""
    day_real: dict = {}
    col = _find_painel_col(rows)

    if col is not None:
        current_date = None
        for row in rows:
            if len(row) <= col:
                continue
            cell_base = str(row[col]).strip()

            m = re.match(r'^(\d{1,2})\s*[-\.]\s*(\w{3})', cell_base.lower())
            if m:
                try:
                    current_date = date(ano, mes_num, int(m.group(1)))
                except ValueError:
                    current_date = None
                continue

            if not cell_base or 'R$' in cell_base:
                continue
            if current_date is None or len(row) <= col + 2:
                continue

            cliente_raw = cell_base.strip().upper()
            if "TOTAL" in cliente_raw or "GERAL" in cliente_raw:
                continue
            v = _parse_num(str(row[col + 2]).strip())
            if v and v > 0 and cliente_raw:
                key = (current_date, _norm(cliente_raw))
                day_real[key] = day_real.get(key, 0.0) + v
        return day_real

    col = _find_painel_col_rotulo(rows)
    if col is None:
        return {}

    for row in rows:
        if len(row) <= col + 2:
            continue
        cliente_raw = str(row[col]).strip().upper()
        if not cliente_raw or 'R$' in cliente_raw:
            continue
        if "TOTAL" in cliente_raw or "GERAL" in cliente_raw:
            continue
        row_date = _parse_date_pt(row[1]) if len(row) > 1 else None
        if row_date is None:
            continue
        v = _parse_num(str(row[col + 2]).strip())
        if v and v > 0:
            key = (row_date, _norm(cliente_raw))
            day_real[key] = day_real.get(key, 0.0) + v

    return day_real


def _parse_month(rows: list[list[str]], mes_nome: str, mes_num: int, ano: int) -> list[dict]:
    """Parse das linhas de uma aba mensal em registros de carga (cargo + 1
    registro CARGO_REAL com o previsto/realizado oficial do mês + registros
    NAO_ALOCADO para lançamentos do painel diário sem carga cadastrada)."""
    previsto_mensal, realizado_mensal, _fim_periodo = _find_resumo_mensal(rows)
    if (ano, mes_num) in REALIZADO_MENSAL_OVERRIDE:
        realizado_mensal = REALIZADO_MENSAL_OVERRIDE[(ano, mes_num)]
    day_realized = _extract_day_realized(rows, mes_num, ano)
    _painel_col = _find_painel_col(rows)

    if realizado_mensal == 0:
        realizado_mensal = sum(day_realized.values())

    resumo_fim_date: date | None = None
    if _fim_periodo is not None:
        try:
            resumo_fim_date = date(ano, _fim_periodo[1], _fim_periodo[0])
        except ValueError:
            resumo_fim_date = None
    if resumo_fim_date is not None:
        _realizado_pos_corte = sum(
            v for (_d, _c), v in day_realized.items() if _d > resumo_fim_date
        )
        realizado_mensal += _realizado_pos_corte

    def _only_frete_col(r: list[str]) -> bool:
        if len(r) <= 6:
            return False
        return (
            not any(r[j].strip() for j in range(6))
            and bool(r[6].strip())
            and not any(r[j].strip() for j in range(7, len(r)))
        )

    _merged_idx: set[int] = set()
    for _i, _r in enumerate(rows):
        if _parse_date_pt(_r[1] if len(_r) > 1 else ""):
            continue
        if not (_only_frete_col(_r) and _first_frete(_r)):
            continue
        _next = rows[_i + 1] if _i + 1 < len(rows) else []
        if _parse_date_pt(_next[1] if len(_next) > 1 else ""):
            _merged_idx.add(_i)

    records = []
    semana_atual = ""
    _last_cargo_date: date | None = None
    _last_destino_raw: str = ""

    for idx, row in enumerate(rows):
        if len(row) < 8:
            continue

        cell0 = row[0].strip()
        if re.search(r"SEMANA\s+\d{2}/\d{2}", _norm(cell0), re.I):
            semana_atual = cell0
            if not _parse_date_pt(row[1] if len(row) > 1 else ""):
                continue

        if "DATA CARREGAMENTO" in _norm(row[1] if len(row) > 1 else ""):
            continue

        data_carga = _parse_date_pt(row[1]) if len(row) > 1 else None

        if not data_carga:
            if idx in _merged_idx and _last_cargo_date:
                data_carga = _last_cargo_date
            else:
                continue

        _last_cargo_date = data_carga

        destino = row[2].strip().upper() if len(row) > 2 else ""
        if not destino or destino in ("DESTINO", ""):
            if _last_destino_raw:
                destino = _last_destino_raw
            else:
                continue
        else:
            _last_destino_raw = destino

        valor_frete = _first_frete(row, limit=_painel_col)

        cliente = ""
        for i in range(4, min(8, len(row))):
            v = row[i].strip()
            if v and "R$" not in v and not _is_date_str(v) and not re.match(r"^\d+[-/]", v):
                cliente = v.upper()
                break
        if not cliente:
            cliente = destino

        local = ""
        for i in range(3, min(7, len(row))):
            v = row[i].strip().upper()
            if v and "R$" not in v and not _is_date_str(v):
                local = v
                break
        local_norm = _norm(local)
        if "IACANGA" in local_norm:
            local_tag = "Iacanga"
        elif "AREALVA" in local_norm:
            local_tag = "Arealva"
        elif "ITAJU" in local_norm:
            local_tag = "Itaju"
        elif "BARIRI" in local_norm:
            local_tag = "Bariri"
        elif "IBITINGA" in local_norm:
            local_tag = "Ibitinga"
        elif local_norm:
            local_tag = "Múltiplas"
        else:
            local_tag = "N/I"

        obs_raw = ""
        status = "Normal"
        for cell in row[6:15]:
            v = cell.strip().upper()
            if not v or "R$" in v:
                continue
            if "CANCEL" in v:
                obs_raw = v; status = "Cancelada"; break
            if any(k in v for k in ["ADIAD", "ADIADO", "ADIADA"]):
                obs_raw = v; status = "Adiada"; break
            if "ARMAZENAGEM" in v:
                obs_raw = v; status = "Armazenagem"; break

        veiculo = ""
        for i in range(3, min(6, len(row))):
            v = row[i].strip()
            if v and not _is_date_str(v) and "R$" not in v:
                vn = _norm(v)
                if any(k in vn for k in ["CARRETA", "TRUCK", "TRANSP", "ACCELO"]):
                    veiculo = v.upper()
                    break
        tipo_veiculo = (
            "Carreta"    if "CARRETA" in _norm(veiculo) else
            "Truck"      if "TRUCK"   in _norm(veiculo) else
            "Acello"     if "ACCELO"  in _norm(veiculo) else
            "Transporte" if "TRANSP"  in _norm(veiculo) else
            "Outro"
        )

        records.append({
            "MES": mes_nome, "MES_NUM": mes_num, "ANO": ano, "SEMANA": semana_atual,
            "DATA": data_carga, "DESTINO": destino, "LOCAL": local_tag,
            "VEICULO": veiculo, "TIPO_VEICULO": tipo_veiculo, "VALOR_FRETE": valor_frete,
            "CLIENTE": cliente,
            "PREVISAO": (
                0.0 if previsto_mensal > 0 and (resumo_fim_date is None or data_carga <= resumo_fim_date)
                else valor_frete
            ),
            "REALIZADO": 0.0,
            "_REAL_KEY": (
                (data_carga, _norm(destino)) if (data_carga, _norm(destino)) in day_realized
                else (data_carga, _norm(cliente)) if (data_carga, _norm(cliente)) in day_realized
                else None
            ),
            "REALIZADO_DIA": 0.0, "DIFERENCA": 0.0, "OBS": obs_raw, "STATUS": status,
        })

    _contagem_chave = Counter(r["_REAL_KEY"] for r in records if r["_REAL_KEY"] is not None)
    for r in records:
        _chave = r.pop("_REAL_KEY", None)
        if _chave is not None:
            r["REALIZADO_DIA"] = day_realized[_chave] / _contagem_chave[_chave]

    _datas_com_carga = {r["DATA"] for r in records}
    _painel_datas_com_carga = sum(
        v for (_d, _c), v in day_realized.items() if _d in _datas_com_carga
    )
    _soma_batida = sum(r["REALIZADO_DIA"] for r in records)
    if _soma_batida > 0 and _painel_datas_com_carga > 0:
        _fator = _painel_datas_com_carga / _soma_batida
        for r in records:
            r["REALIZADO_DIA"] *= _fator

    for (_data_orfa, _cliente_orfa), _v_orfa in day_realized.items():
        if _data_orfa in _datas_com_carga:
            continue
        records.append({
            "MES": mes_nome, "MES_NUM": mes_num, "ANO": ano, "SEMANA": "NAO_ALOCADO",
            "DATA": _data_orfa, "DESTINO": "SEM PREVISAO", "LOCAL": "", "VEICULO": "",
            "TIPO_VEICULO": "", "VALOR_FRETE": 0.0, "CLIENTE": _cliente_orfa,
            "PREVISAO": 0.0, "REALIZADO": 0.0, "REALIZADO_DIA": _v_orfa, "DIFERENCA": 0.0,
            "OBS": "Realizado do painel diário sem carga cadastrada no mês",
            "STATUS": "NAO_ALOCADO",
        })

    if realizado_mensal > 0 or previsto_mensal > 0:
        proxy_date = _last_cargo_date or date(ano, mes_num, 28)
        records.append({
            "MES": mes_nome, "MES_NUM": mes_num, "ANO": ano, "SEMANA": "",
            "DATA": proxy_date, "DESTINO": mes_nome, "LOCAL": "", "VEICULO": "",
            "TIPO_VEICULO": "", "VALOR_FRETE": 0.0, "CLIENTE": mes_nome,
            "PREVISAO": previsto_mensal, "REALIZADO": realizado_mensal,
            "REALIZADO_DIA": 0.0, "DIFERENCA": 0.0, "OBS": "", "STATUS": "CARGO_REAL",
        })

    return records


def carregar_cargas() -> pd.DataFrame:
    """DataFrame de Previsão de Cargas (todos os meses disponíveis, previsto x
    realizado). Vazio se nenhuma aba mensal estiver disponível."""
    all_records: list[dict] = []
    for mes_nome, mes_num, ano in MESES_DISPONIVEIS:
        rows = _fetch_csv(mes_nome)
        if not rows:
            continue
        try:
            all_records.extend(_parse_month(rows, mes_nome, mes_num, ano))
        except Exception:
            continue

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df["DATA"] = pd.to_datetime(df["DATA"])
    df["DIA_SEMANA"] = df["DATA"].dt.day_name()

    alias = {"NIAZITEX": "NIAZITEX", "NIAZITTEX": "NIAZITEX", "NC INDUSTRIA": "NIAZITEX"}
    df["CLIENTE_NORM"] = df["CLIENTE"].apply(lambda x: alias.get(_norm(x), _norm(x)))
    df["DESTINO_NORM"] = df["DESTINO"].apply(lambda x: alias.get(_norm(x), _norm(x)))

    meses_com_real = set(df.loc[df["STATUS"] == "CARGO_REAL", "MES"].tolist())
    df["TEM_REALIZADO"] = df["MES"].isin(meses_com_real) & (df["STATUS"] != "CARGO_REAL")

    return df


# ── Filtros ───────────────────────────────────────────────────────────────────
def opcoes_filtro(df: pd.DataFrame) -> dict:
    """Opções para os multiselects — exclui os registros sintéticos
    (CARGO_REAL/NAO_ALOCADO) das opções, mesmo que continuem no dataset."""
    base = df[df["STATUS"] != "NAO_ALOCADO"]
    return {
        "meses": df["MES"].unique().tolist(),
        "destinos": sorted(base["DESTINO_NORM"].unique()),
        "locais": sorted(base["LOCAL"].unique()),
        "status": sorted(s for s in base["STATUS"].unique() if s not in ("SEMANA_TOTAL", "CARGO_REAL")),
    }


def aplicar_filtros(df_raw: pd.DataFrame, *, meses=None, destinos=None, locais=None,
                     status=None, mostrar_sem_real: bool = False) -> pd.DataFrame:
    """Cargos (CARGO_REAL/NAO_ALOCADO) ficam de fora do filtro de destino/local/
    status — são registros sintéticos com o Previsto/Realizado oficial do mês,
    que precisa continuar batendo mesmo com filtros aplicados nos cargos."""
    _df_mes = df_raw[df_raw["MES"].isin(meses)] if meses else df_raw
    df_real_fixo = _df_mes[_df_mes["STATUS"] == "CARGO_REAL"].copy()
    df_naoaloc_fixo = _df_mes[_df_mes["STATUS"] == "NAO_ALOCADO"].copy()
    df_cargo_filt = _df_mes[~_df_mes["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO"])].copy()

    if destinos:
        df_cargo_filt = df_cargo_filt[df_cargo_filt["DESTINO_NORM"].isin(destinos)]
    if locais:
        df_cargo_filt = df_cargo_filt[df_cargo_filt["LOCAL"].isin(locais)]
    if status:
        df_cargo_filt = df_cargo_filt[df_cargo_filt["STATUS"].isin(status)]
    if not mostrar_sem_real:
        _meses_com_prev_ofic = set(df_real_fixo[df_real_fixo["PREVISAO"] > 0]["MES_NUM"])
        df_cargo_filt = df_cargo_filt[
            (df_cargo_filt["PREVISAO"] > 0) | df_cargo_filt["MES_NUM"].isin(_meses_com_prev_ofic)
        ]

    return pd.concat([df_real_fixo, df_naoaloc_fixo, df_cargo_filt], ignore_index=True)


# ── KPIs ──────────────────────────────────────────────────────────────────────
def kpis_globais(df: pd.DataFrame) -> dict:
    df_cargos = df[df["STATUS"].isin(["Normal", "Cancelada", "Adiada", "Armazenagem"])]
    df_realizados = df[df["STATUS"] == "CARGO_REAL"]

    total_prev = float(df["PREVISAO"].sum())
    total_real = float(df_realizados["REALIZADO"].sum())
    diferenca_g = total_real - total_prev if total_prev > 0 else 0.0

    meses_com_real = set(
        df_realizados.groupby("MES_NUM")["REALIZADO"].sum().pipe(lambda s: s[s > 0].index)
    )
    _prev_adh = df[df["MES_NUM"].isin(meses_com_real)]["PREVISAO"].sum()
    _real_adh = df_realizados[df_realizados["MES_NUM"].isin(meses_com_real)]["REALIZADO"].sum()
    aderencia_g = (_real_adh / _prev_adh * 100) if _prev_adh > 0 else 0.0

    return {
        "total_prev": total_prev,
        "total_real": total_real,
        "diferenca": diferenca_g,
        "diferenca_cor": "pos" if diferenca_g >= 0 else "neg",
        "aderencia": aderencia_g,
        "aderencia_cor": "pos" if aderencia_g >= 95 else ("neg" if aderencia_g < 80 else "neu"),
        "n_clientes": int(df_cargos["DESTINO_NORM"].nunique()),
        "n_canceladas": int((df_cargos["STATUS"] == "Cancelada").sum()),
        "n_adiadas": int((df_cargos["STATUS"] == "Adiada").sum()),
    }


def estimativa_mes_atual(df_raw: pd.DataFrame) -> dict | None:
    return _estimativa_mes_atual(df_raw)


# ── Gráficos ──────────────────────────────────────────────────────────────────
def previsao_vs_realizado_mes(df: pd.DataFrame) -> dict:
    df_realizados = df[df["STATUS"] == "CARGO_REAL"]
    _prev_mes = df.groupby(["MES", "MES_NUM"])["PREVISAO"].sum().reset_index()
    _real_mes = df_realizados.groupby(["MES", "MES_NUM"])["REALIZADO"].sum().reset_index()
    g = (_prev_mes.merge(_real_mes, on=["MES", "MES_NUM"], how="outer")
         .fillna(0).sort_values("MES_NUM"))
    g["ADERENCIA"] = g.apply(
        lambda r: r["REALIZADO"] / r["PREVISAO"] * 100 if r["PREVISAO"] > 0 else 0, axis=1
    )
    return {
        "meses": g["MES"].tolist(),
        "previsao": [float(v) for v in g["PREVISAO"]],
        "realizado": [float(v) for v in g["REALIZADO"]],
        "aderencia": [round(float(v), 1) for v in g["ADERENCIA"]],
    }


def por_destino(df: pd.DataFrame, limite: int = 12) -> dict:
    """Usa VALOR_FRETE (nunca zerado) em vez de PREVISAO: em meses com previsto
    oficial, PREVISAO de cada carga individual é zerada (o total já vem do
    registro CARGO_REAL), o que deixaria esta quebra por cliente vazia."""
    g = (df[df["TEM_REALIZADO"] & (df["VALOR_FRETE"] > 0)]
         .groupby("DESTINO_NORM")
         .agg(PREVISAO=("VALOR_FRETE", "sum"), N_CARGAS=("DATA", "count"))
         .reset_index().sort_values("PREVISAO", ascending=False).head(limite)
         .sort_values("PREVISAO", ascending=True))
    return {"y": g["DESTINO_NORM"].tolist(), "x": [float(v) for v in g["PREVISAO"]]}


def por_local(df: pd.DataFrame) -> dict:
    g = (df[df["VALOR_FRETE"] > 0].groupby("LOCAL")
         .agg(PREVISAO=("VALOR_FRETE", "sum"), N=("DATA", "count"))
         .reset_index().sort_values("PREVISAO", ascending=False))
    return {"labels": g["LOCAL"].tolist(), "valores": [float(v) for v in g["PREVISAO"]]}


def aderencia_por_mes(df: pd.DataFrame) -> dict:
    """Aderência por total mensal — não filtra por STATUS != CARGO_REAL: em
    meses com previsto oficial o total mora no registro CARGO_REAL (fretes
    individuais ficam zerados)."""
    _prev_adh = df[df["PREVISAO"] > 0].groupby("MES")["PREVISAO"].sum()
    _real_adh = df[df["STATUS"] == "CARGO_REAL"].groupby("MES")["REALIZADO"].sum()
    g = pd.DataFrame({"PREVISAO": _prev_adh, "REALIZADO": _real_adh}).dropna().reset_index()
    g = g[g["PREVISAO"] > 0].copy()
    if g.empty:
        return {"meses": [], "aderencia": [], "cores": []}
    g["ADERENCIA"] = g["REALIZADO"] / g["PREVISAO"] * 100
    g = g.sort_values("ADERENCIA", ascending=True)
    cores = [
        CORES["diferenca_pos"] if v >= 95 else (CORES["diferenca_neg"] if v < 80 else CORES["accent"])
        for v in g["ADERENCIA"]
    ]
    return {
        "meses": g["MES"].tolist(),
        "aderencia": [round(float(v), 1) for v in g["ADERENCIA"]],
        "cores": cores,
    }


def evolucao_semanal(df: pd.DataFrame) -> dict:
    """Previsão/N só olham cargas com frete (VALOR_FRETE > 0) — Armazenagem ou
    frete subcontratado não entram na previsão de custo. Realizado soma TODAS
    as cargas da semana (inclusive frete zero): o painel diário atribui o
    Realizado por (data, cliente), e um cliente pode ter uma carga de
    Armazenagem e outra normal no mesmo dia."""
    df_semana = _tabela_semanal(df)
    if df_semana.empty:
        return {"labels": [], "previsao": [], "realizado": []}
    chart = df_semana[df_semana["SEMANA"] != "NAO_ALOCADO"]
    return {
        "labels": chart["LABEL"].tolist(),
        "previsao": [float(v) for v in chart["PREVISAO"]],
        "realizado": [float(v) for v in chart["REALIZADO"]],
    }


def _tabela_semanal(df: pd.DataFrame) -> pd.DataFrame:
    _semana_base = df[df["TEM_REALIZADO"]]
    df_week_prev = (
        _semana_base[_semana_base["VALOR_FRETE"] > 0]
        .groupby(["MES", "MES_NUM", "SEMANA"])
        .agg(PREVISAO=("VALOR_FRETE", "sum"), N=("DATA", "count"))
        .reset_index()
    )
    df_week_real = (
        _semana_base
        .groupby(["MES", "MES_NUM", "SEMANA"])
        .agg(REALIZADO=("REALIZADO_DIA", "sum"), INICIO=("DATA", "min"), FIM=("DATA", "max"))
        .reset_index()
    )
    if df_week_prev.empty and df_week_real.empty:
        return pd.DataFrame()
    df_week = df_week_prev.merge(df_week_real, on=["MES", "MES_NUM", "SEMANA"], how="outer")
    df_week[["PREVISAO", "N"]] = df_week[["PREVISAO", "N"]].fillna(0)
    df_week = df_week.sort_values("INICIO")

    def _semana_label(row) -> str:
        if row["SEMANA"] == "NAO_ALOCADO":
            return f"{row['INICIO'].strftime('%d/%m')} a {row['FIM'].strftime('%d/%m')} (sem previsão)"
        m = re.search(r"(\d{2}/\d{2})\s*A\s*(\d{2}/\d{2})", _norm(row["SEMANA"]))
        if m:
            return f"{m.group(1)} a {m.group(2)}"
        return f"{row['INICIO'].strftime('%d/%m')} a {row['FIM'].strftime('%d/%m')}"

    df_week["LABEL"] = df_week.apply(_semana_label, axis=1)
    return df_week


def detalhamento_semana_tabela(df: pd.DataFrame) -> list[dict]:
    df_week = _tabela_semanal(df)
    if df_week.empty:
        return []
    df_week = df_week.copy()
    df_week["DIFERENCA"] = df_week["REALIZADO"] - df_week["PREVISAO"]
    df_week["ADERENCIA"] = df_week.apply(
        lambda r: r["REALIZADO"] / r["PREVISAO"] * 100 if r["PREVISAO"] > 0 else None, axis=1
    )
    linhas = []
    for _, r in df_week.iterrows():
        linhas.append({
            "mes": r["MES"], "semana": r["LABEL"], "cargas": int(r["N"]),
            "previsao": float(r["PREVISAO"]), "realizado": float(r["REALIZADO"]),
            "diferenca": float(r["DIFERENCA"]),
            "aderencia": None if pd.isna(r["ADERENCIA"]) else round(float(r["ADERENCIA"]), 1),
        })
    return linhas


def por_tipo_veiculo(df: pd.DataFrame) -> dict:
    g = (df[~df["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO"]) & (df["TIPO_VEICULO"] != "Outro")]
         .groupby("TIPO_VEICULO")
         .agg(N=("DATA", "count"), PREVISAO=("VALOR_FRETE", "sum"))
         .reset_index().sort_values("N", ascending=False))
    return {
        "tipos": g["TIPO_VEICULO"].tolist(),
        "n": [int(v) for v in g["N"]],
        "previsao": [float(v) for v in g["PREVISAO"]],
    }


def timeline(df: pd.DataFrame) -> dict:
    g = (df[~df["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO"])]
         .groupby(["DATA", "STATUS"])
         .agg(PREVISAO=("VALOR_FRETE", "sum"), N=("DESTINO", "count"))
         .reset_index())
    series = []
    for status_v, grp in g.groupby("STATUS"):
        series.append({
            "status": status_v,
            "x": [d.strftime("%Y-%m-%d") for d in grp["DATA"]],
            "y": [float(v) for v in grp["PREVISAO"]],
            "n": [int(v) for v in grp["N"]],
        })
    return {"series": series}


def ocorrencias(df: pd.DataFrame) -> dict:
    df_ocorr = df[df["STATUS"].isin(["Cancelada", "Adiada"])].copy()
    if df_ocorr.empty:
        return {"vazio": True}
    valor_impacto = float(df_ocorr["VALOR_FRETE"].sum())
    n_cancel = int((df_ocorr["STATUS"] == "Cancelada").sum())
    n_adiad = int((df_ocorr["STATUS"] == "Adiada").sum())

    por_mes = df_ocorr.groupby(["MES", "STATUS"]).size().reset_index(name="N")
    meses_ordem = list(dict.fromkeys(df_ocorr.sort_values("DATA")["MES"]))
    por_mes_series = []
    for status_v in ["Cancelada", "Adiada"]:
        gm = por_mes[por_mes["STATUS"] == status_v].set_index("MES").reindex(meses_ordem)["N"].fillna(0)
        por_mes_series.append({"status": status_v, "y": [int(v) for v in gm]})

    por_destino_g = (df_ocorr.groupby("DESTINO_NORM")
                     .agg(N=("DATA", "count"), PREVISAO=("VALOR_FRETE", "sum"))
                     .reset_index().sort_values("N", ascending=True))

    return {
        "vazio": False,
        "total": len(df_ocorr), "n_cancel": n_cancel, "n_adiadas": n_adiad,
        "valor_impacto": valor_impacto,
        "por_mes_meses": meses_ordem, "por_mes_series": por_mes_series,
        "por_destino_y": por_destino_g["DESTINO_NORM"].tolist(),
        "por_destino_x": [int(v) for v in por_destino_g["N"]],
    }


_DIAS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DIAS_PT = {"Monday": "Segunda", "Tuesday": "Terça", "Wednesday": "Quarta",
            "Thursday": "Quinta", "Friday": "Sexta", "Saturday": "Sábado", "Sunday": "Domingo"}


def heatmap_dia_semana(df: pd.DataFrame) -> dict:
    base = df[df["TEM_REALIZADO"] & (df["VALOR_FRETE"] > 0)]
    if base.empty:
        return {"dias": [], "meses": [], "z": []}
    piv = base.pivot_table(index="DIA_SEMANA", columns="MES", values="VALOR_FRETE",
                            aggfunc="sum", fill_value=0)
    piv = piv.reindex([d for d in _DIAS_ORDER if d in piv.index])
    mes_order = [m for m, _, _ in MESES_DISPONIVEIS if m in piv.columns]
    piv = piv[mes_order]
    return {
        "dias": [_DIAS_PT.get(d, d) for d in piv.index],
        "meses": piv.columns.tolist(),
        "z": [[float(v) for v in row] for row in piv.values],
    }


def detalhe_tabela(df: pd.DataFrame, busca: str = "") -> list[dict]:
    d = df[df["STATUS"] != "CARGO_REAL"].copy()
    if busca.strip():
        b = busca.upper()
        mask = d["DESTINO_NORM"].str.contains(b, na=False) | d["CLIENTE"].str.contains(b, na=False)
        d = d[mask]
    d = d.sort_values("DATA", ascending=False)
    linhas = []
    for _, r in d.iterrows():
        linhas.append({
            "mes": r["MES"], "data": r["DATA"].strftime("%d/%m/%Y"),
            "destino": r["DESTINO"], "local": r["LOCAL"], "tipo_veiculo": r["TIPO_VEICULO"],
            "cliente": r["CLIENTE"], "previsao": float(r["VALOR_FRETE"]),
            "realizado": float(r["REALIZADO_DIA"]),
            "diferenca": float(r["REALIZADO_DIA"] - r["VALOR_FRETE"]),
            "status": r["STATUS"], "obs": r["OBS"],
        })
    return linhas


def resumo_mensal_tabela(df: pd.DataFrame) -> list[dict]:
    g = (df.groupby("MES")
         .agg(N_Registros=("DATA", "count"), Previsao=("PREVISAO", "sum"),
              Realizado=("REALIZADO", "sum"),
              Canceladas=("STATUS", lambda x: (x == "Cancelada").sum()),
              Adiadas=("STATUS", lambda x: (x == "Adiada").sum()),
              Destinos=("DESTINO_NORM", "nunique"))
         .reset_index())
    linhas = []
    for _, r in g.iterrows():
        aderencia = (r["Realizado"] / r["Previsao"] * 100) if r["Previsao"] > 0 else None
        linhas.append({
            "mes": r["MES"], "registros": int(r["N_Registros"]),
            "previsao": float(r["Previsao"]), "realizado": float(r["Realizado"]),
            "canceladas": int(r["Canceladas"]), "adiadas": int(r["Adiadas"]),
            "destinos": int(r["Destinos"]),
            "aderencia": None if aderencia is None else round(float(aderencia), 1),
            "diferenca": float(r["Realizado"] - r["Previsao"]),
        })
    return linhas
