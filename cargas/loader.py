"""
Loader de Previsão de Cargas (previsão x realizado).

Portado quase literalmente de utils/cargas_loader.py (Streamlit original) —
mantém TODOS os bugfixes documentados nos comentários (planilha com um layout
manual muito irregular: cabeçalhos de semana, células mescladas, painel
diário de realizado numa coluna que muda de posição por mês, linha de resumo
mensal em formatos diferentes, etc.). Só a camada de I/O muda: usa
integracao.sheets_client.get_raw_sheet (cache em disco) em vez de urllib.
"""

from __future__ import annotations

import csv
import calendar
import io
import logging
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta

import pandas as pd

from integracao import db_reader
from integracao.sheets_client import get_raw_sheet
from integracao.fontes import FONTES

logger = logging.getLogger(__name__)

CARGAS_CACHE_TTL = 300  # segundos

MESES_NOMES_PT = {
    1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL", 5: "MAIO", 6: "JUNHO",
    7: "JULHO", 8: "AGOSTO", 9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO",
}

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


def _meses_disponiveis(ano_inicio: int = 2026, mes_inicio: int = 1) -> list[tuple[str, int, int]]:
    """Gera (NOME, mes, ano) de ano_inicio/mes_inicio até o mês atual — assim
    não precisa editar uma lista fixa todo mês."""
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

# Override manual do REALIZADO mensal para meses fechados onde o lançamento
# diário na planilha ficou incompleto e não é mais recuperável — o valor vem
# do relatório "Acompanhamento Mensal" (fechamento por empresa), fonte confiável
# pra esses meses. Meses fora deste dict continuam vindo 100% da linha de
# resumo da planilha (_find_resumo_mensal).
REALIZADO_MENSAL_OVERRIDE: dict[tuple[int, int], float] = {
    (2026, 5): 4_065_134.69,  # Maio/2026
}


# ── Helpers de parsing ─────────────────────────────────────────────────────────
def _norm(s) -> str:
    return (
        unicodedata.normalize("NFD", str(s))
        .encode("ascii", "ignore").decode()
        .upper().strip()
    )


def _parse_money(s) -> float | None:
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


def _parse_date_pt(s) -> date | None:
    raw = str(s).strip()
    sl = raw.lower()

    # Formato primário: "quinta-feira, junho 1, 2026" (gviz PT locale)
    m = re.search(r"(\w+),\s+(\w+)\s+(\d+),\s+(\d{4})", sl)
    if m:
        month = MESES_PT.get(m.group(2))
        if month:
            try:
                return date(int(m.group(4)), month, int(m.group(3)))
            except ValueError:
                pass

    # Fallback: "dd/mm/yyyy" ou "d/m/yyyy" (BR) ou "m/d/yyyy" (US)
    m2 = re.match(r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$", raw.strip())
    if m2:
        a, b, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            if a > 12:
                return date(y, b, a)
            elif b > 12:
                return date(y, a, b)
            else:
                return date(y, b, a)   # DD/MM/YYYY
        except ValueError:
            pass

    # Fallback: "yyyy-mm-dd" (ISO)
    m3 = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$", raw.strip())
    if m3:
        try:
            return date(int(m3.group(1)), int(m3.group(2)), int(m3.group(3)))
        except ValueError:
            pass

    return None


def _is_date_str(s: str) -> bool:
    return bool(re.search(r"\d{4}", s)) and any(
        mes in s.lower() for mes in MESES_PT
    )


def _estimativa_mes_atual(df_raw: pd.DataFrame) -> dict | None:
    """Projeta o fechamento (Previsto e Realizado) do mês corrente.

    1. Previsto Projetado = Previsto lançado / dias cobertos pelos lançamentos
       x dias totais do mês (run-rate). "Dias cobertos" é o intervalo entre a
       primeira e a última carga já lançada no mês (a planilha é preenchida em
       blocos semanais, não dia a dia).
    2. Aderência Média = média simples de (Realizado/Previsto) dos 2 últimos
       meses fechados (com Realizado oficial > 0) antes do mês corrente.
    3. Realizado Estimado = Previsto Projetado x Aderência Média.
    """
    hoje = date.today()
    ano_atual, mes_atual = hoje.year, hoje.month

    df_mes_atual = df_raw[(df_raw["ANO"] == ano_atual) & (df_raw["MES_NUM"] == mes_atual)]
    if df_mes_atual.empty:
        return None

    previsto_lancado = df_mes_atual["PREVISAO"].sum()
    dias_totais = calendar.monthrange(ano_atual, mes_atual)[1]

    df_cargas_mes = df_mes_atual[~df_mes_atual["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO", "CLIENTE_REAL"])]
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
        "realizado_estimado": realizado_estimado,
        "dias_corridos": dias_cobertos,
        "dias_totais": dias_totais,
        "n_meses_base": len(ultimos_2),
    }


# ── Loader ────────────────────────────────────────────────────────────────────
def _fetch_csv(sheet_id: str, sheet_name: str, ttl: int) -> list[list[str]]:
    conteudo = get_raw_sheet(sheet_id, sheet_name, ttl=ttl)
    if not conteudo:
        return []
    return list(csv.reader(io.StringIO(conteudo)))


def _first_frete(row: list[str], limit: int | None = None) -> float:
    """Primeiro R$ positivo em cols 5 até `limit` (exclusivo, default col 10).

    Para a maioria dos meses o frete está em col[6], mas JANEIRO usa col[7]
    (layout com coluna MOTORISTA extra). Varredura dinâmica evita hard-code.

    `limit` deve ser a coluna-base do painel diário (_find_painel_col) quando
    conhecida — sem isso, cargas com a própria célula de frete vazia (ex.:
    Armazenagem, frete subcontratado) acabam lendo o Previsto do painel diário
    de OUTRO cliente no mesmo dia como se fosse o frete dessa carga.
    """
    hi = min(limit, len(row)) if limit is not None else min(10, len(row))
    for j in range(5, hi):
        v = _parse_money(row[j])
        if v and abs(v) > 0:
            return abs(v)
    return 0.0


def _parse_num(s) -> float | None:
    """Parse numérico permissivo — aceita valor com ou sem prefixo R$ ('2.450.000,00')."""
    s = str(s).strip()
    if not s or s in ("-", "R$", "R$  -", "R$-"):
        return None
    clean = re.sub(r"[R$\s\-]", "", s).replace(".", "").replace(",", ".")
    try:
        v = float(clean)
        return v if v > 0 else None
    except ValueError:
        return None


def _semana_intervalo(label: str, ano: int) -> tuple[date, date] | None:
    """(início, fim) de um rótulo 'SEMANA DD/MM A DD/MM' — usado como fallback
    de data pra cargas sem data própria na linha (algumas semanas listam só
    destino/veículo/frete, sem uma coluna de data por carga; sem esse
    fallback a previsão inteira da semana some — reportado 2026-07-27)."""
    m = re.search(r"(\d{2})/(\d{2})\s*A\s*(\d{2})/(\d{2})", _norm(label))
    if not m:
        return None
    try:
        return (date(ano, int(m.group(2)), int(m.group(1))),
                date(ano, int(m.group(4)), int(m.group(3))))
    except ValueError:
        return None


def _find_resumo_periodo_fim(label: str) -> tuple[int, int] | None:
    """Extrai (dia, mês) do fim do período coberto por um rótulo tipo
    "Total geral (01 a 11/07)" — para não tratar um total parcial como se
    fosse o mês inteiro."""
    m = re.search(r"\(\s*\d{1,2}\s*a\s*(\d{1,2})/(\d{1,2})\s*\)", label)
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _somar_semanas_pendentes(rows: list[list[str]], from_idx: int) -> tuple[float, float]:
    """Soma qualquer subtotal 'Total semana NN' que apareça DEPOIS da linha de
    resumo mensal/quinzenal já capturada (`from_idx`).

    A planilha fecha o mês em blocos (quinzena, às vezes só "semana"), e o
    bloco mais recente só ganha uma linha de total quando a quinzena/mês
    termina. Enquanto o mês está em andamento, os dias mais novos (ex.:
    segunda quinzena ainda incompleta) só aparecem como semanas soltas depois
    do último total já fechado — sem essa soma, o comparativo Previsto ×
    Realizado do mês corrente fica defasado, faltando os dias mais recentes
    (reportado pelo usuário 2026-07-24: Julho mostrava só a 1ª quinzena)."""
    prev_total, real_total = 0.0, 0.0
    for row in rows[from_idx + 1:]:
        if _parse_date_pt(row[1] if len(row) > 1 else ""):
            continue
        label_idx = next(
            (j for j, cell in enumerate(row)
             if "TOTAL" in _norm(cell) and "SEMANA" in _norm(cell)), None)
        if label_idx is None:
            continue
        prev_col, real_col = label_idx + 1, label_idx + 2
        if len(row) <= real_col:
            continue
        r = _parse_money(row[real_col])
        if not r:
            continue
        p = _parse_money(row[prev_col]) if len(row) > prev_col else None
        prev_total += abs(p) if p else 0.0
        real_total += abs(r)
    return prev_total, real_total


def _find_resumo_mensal(rows: list[list[str]]) -> tuple[float, float, tuple[int, int] | None]:
    """Retorna (previsto_mensal, realizado_mensal, fim_periodo) a partir da linha
    de resumo da planilha. fim_periodo = (dia, mês) até onde o resumo cobre,
    quando o rótulo declara um intervalo parcial; None quando cobre o mês
    inteiro (ou quando não dá pra saber).

    Estratégia:
    1. Cabeçalho "Previsto total" → dois valores > R$1.5M na mesma linha.
    2. Linha com 'GERAL' em cols 8-14 → col[GERAL+2] é o realizado; col[GERAL+1]
       é o previsto quando presente.
    3. Maior valor não-redondo (not % 1.000) > R$1M em cols 8-15 de linhas
       não-data → realizado oficial do mês.

    Em qualquer estratégia, soma ainda os totais de semana que apareçam DEPOIS
    da linha de resumo encontrada (`_somar_semanas_pendentes`) — cobre o caso
    do mês em andamento, cuja quinzena/semana mais recente ainda não fechou
    numa linha de total maior.
    """
    for idx, row in enumerate(rows):
        if _parse_date_pt(row[1] if len(row) > 1 else ""):
            continue
        big: list[float] = []
        for cell in row:
            v = _parse_num(str(cell))
            if v and v > 1_500_000:
                big.append(v)
        if len(big) >= 2:
            extra_p, extra_r = _somar_semanas_pendentes(rows, idx)
            return (big[0] + extra_p, big[1] + extra_r, None)

    for idx, row in enumerate(rows):
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
                        extra_p, extra_r = _somar_semanas_pendentes(rows, idx)
                        return ((abs(prev_v) if prev_v else 0.0) + extra_p,
                                abs(v) + extra_r, fim_periodo)

    best = 0.0
    best_idx = None
    for idx, row in enumerate(rows):
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
                best, best_idx = av, idx
    if best > 0:
        extra_p, extra_r = _somar_semanas_pendentes(rows, best_idx)
        return (extra_p, best + extra_r, None)

    return (0.0, 0.0, None)


def _find_painel_col(rows: list[list[str]]) -> int | None:
    """Detecta a coluna do painel diário de realizado ("DD-mmm.") — a posição
    muda de mês para mês na planilha. Usa a coluna mais frequente entre todas
    as células que casam com o padrão de cabeçalho de dia."""
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
    localiza a coluna pelo rótulo de cabeçalho "REALIZADO" na 1ª linha; o
    nome do cliente fica 2 colunas antes."""
    if not rows:
        return None
    for j, cell in enumerate(rows[0]):
        if _norm(cell) == "REALIZADO" and j >= 2:
            return j - 2
    return None


def _extract_day_realized(rows: list[list[str]], mes_num: int, ano: int) -> tuple[dict, dict]:
    """Lê o painel direito do CSV. Retorna (day_real, totais_semana_fim):
      - day_real: {(data, cliente_norm): realizado}, célula a célula.
      - totais_semana_fim: {última_data_da_semana: total} — algumas semanas
        (a partir da 29, no formato visto em Julho/2026) têm uma linha "Total
        semana N" digitada à mão logo após o último dia, que às vezes diverge
        da soma das células diárias da mesma semana (alguém corrigiu o total
        sem atualizar todos os lançamentos). Essa linha é a mais confiável —
        é o número usado no relatório oficial "Realizado x Previsto" por
        cliente/semana — então vira override do total da semana em
        _parse_month, em vez de confiarmos só na soma das células."""
    day_real: dict = {}
    totais_semana_fim: dict = {}
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
            if _norm(cliente_raw).startswith("TOTAL SEMANA"):
                v_semana = _parse_num(str(row[col + 2]).strip())
                if v_semana and v_semana > 0:
                    totais_semana_fim[current_date] = v_semana
                continue
            if "TOTAL" in cliente_raw or "GERAL" in cliente_raw:
                continue
            v = _parse_num(str(row[col + 2]).strip())
            if v and v > 0 and cliente_raw:
                key = (current_date, _norm(cliente_raw))
                day_real[key] = day_real.get(key, 0.0) + v
        return day_real, totais_semana_fim

    col = _find_painel_col_rotulo(rows)
    if col is None:
        return {}, {}

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

    return day_real, {}


def _parse_month(rows: list[list[str]], mes_nome: str, mes_num: int, ano: int) -> list[dict]:
    """Parse rows de uma aba mensal em registros de carga.

    Gera dois tipos de registro:
      1. Linhas de CARGO (com data em col[1]).
      2. UM registro CARGO_REAL por mês com o REALIZADO total oficial.
    """
    previsto_mensal, realizado_mensal, _fim_periodo = _find_resumo_mensal(rows)
    if (ano, mes_num) in REALIZADO_MENSAL_OVERRIDE:
        realizado_mensal = REALIZADO_MENSAL_OVERRIDE[(ano, mes_num)]
    day_realized, totais_semana_fim = _extract_day_realized(rows, mes_num, ano)
    _painel_col = _find_painel_col(rows)

    # Fallback para o mês corrente/em andamento: a linha de resumo só é
    # preenchida perto do fim do mês. Enquanto isso, usa a soma do painel
    # diário para não deixar o Realizado do mês travado em zero.
    if realizado_mensal == 0:
        realizado_mensal = sum(day_realized.values())

    # Resumo parcial: soma por cima o que foi lançado depois da data de corte
    # do resumo, em vez de descartar.
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

    # Pré-computa índices de "linha mesclada": linha sem data onde SOMENTE col[6]
    # tem valor E a próxima linha tem data (célula de frete mesclada no Sheets).
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
    _semana_atual_intervalo: tuple[date, date] | None = None
    _last_cargo_date: date | None = None
    _last_destino_raw: str = ""
    # Dias de semanas que usaram o fallback de data abaixo (dia → rótulo da
    # semana) — precisam contar como "tem carga"/"desta semana" pro painel
    # diário inteiro, senão os dias sem o fallback em si viram "não alocado"
    # por engano e a reconciliação da semana ignora o painel deles.
    _dias_fallback_semana: dict = {}

    for idx, row in enumerate(rows):
        if len(row) < 8:
            continue

        cell0 = row[0].strip()
        if re.search(r"SEMANA\s+\d{2}/\d{2}", _norm(cell0), re.I):
            semana_atual = cell0
            _semana_atual_intervalo = _semana_intervalo(cell0, ano)
            if not _parse_date_pt(row[1] if len(row) > 1 else ""):
                continue

        if "DATA CARREGAMENTO" in _norm(row[1] if len(row) > 1 else ""):
            continue

        data_carga = _parse_date_pt(row[1]) if len(row) > 1 else None

        if not data_carga:
            if idx in _merged_idx and _last_cargo_date:
                data_carga = _last_cargo_date
            elif (
                _semana_atual_intervalo is not None and len(row) > 2 and row[2].strip()
                and row[2].strip().upper() != "DESTINO"
                and _first_frete(row, limit=_painel_col) > 0
            ):
                # Semana com layout sem data por carga (só destino/veículo/
                # frete) — usa o início da semana como data aproximada, só
                # pra a previsão não sumir inteira (fica sem dia exato, mas
                # entra na semana certa). Marca a semana inteira como "com
                # carga" pro painel diário não tratar os outros dias como
                # órfãos (reportado 2026-07-27).
                data_carga = _semana_atual_intervalo[0]
                _ini, _fim = _semana_atual_intervalo
                _d = _ini
                while _d <= _fim:
                    _dias_fallback_semana[_d] = semana_atual
                    _d += timedelta(days=1)
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

    # Reconcilia proporcionalmente para que a soma das cargas casadas bata com
    # o total do painel diário DAS MESMAS DATAS que já têm carga cadastrada.
    # Feito POR SEMANA (não pelo mês inteiro): se algumas cargas de uma semana
    # não acham par exato no painel (destino/cliente com grafia diferente),
    # a correção precisa ficar dentro da própria semana — senão o excesso
    # "vaza" e infla/esvazia as semanas vizinhas do mesmo mês (reportado
    # 2026-07-27: dinheiro da semana 29 aparecendo somado na semana 28).
    _datas_com_carga = {r["DATA"] for r in records} | set(_dias_fallback_semana)
    _semana_por_data = {**_dias_fallback_semana, **{r["DATA"]: r["SEMANA"] for r in records}}
    _soma_batida_semana: dict[str, float] = {}
    for r in records:
        _soma_batida_semana[r["SEMANA"]] = _soma_batida_semana.get(r["SEMANA"], 0.0) + r["REALIZADO_DIA"]
    _painel_semana: dict[str, float] = {}
    for (_d, _c), v in day_realized.items():
        _sem = _semana_por_data.get(_d)
        if _sem is not None:
            _painel_semana[_sem] = _painel_semana.get(_sem, 0.0) + v

    # Quando a planilha traz uma linha "Total semana N" pro último dia da
    # semana, ela é mais confiável que a soma das células diárias acima (ver
    # docstring de _extract_day_realized) — vira o alvo da reconciliação.
    if totais_semana_fim:
        _fim_por_semana: dict[str, date] = {}
        for _d, _sem in _semana_por_data.items():
            if _sem is None:
                continue
            if _sem not in _fim_por_semana or _d > _fim_por_semana[_sem]:
                _fim_por_semana[_sem] = _d
        for _sem, _fim in _fim_por_semana.items():
            if _fim in totais_semana_fim:
                _painel_semana[_sem] = totais_semana_fim[_fim]

    for r in records:
        _soma_b = _soma_batida_semana.get(r["SEMANA"], 0.0)
        _painel_s = _painel_semana.get(r["SEMANA"], 0.0)
        if _soma_b > 0 and _painel_s > 0:
            r["REALIZADO_DIA"] *= _painel_s / _soma_b

    # Lançamentos do painel diário em datas sem NENHUMA carga cadastrada no mês
    # geram um registro "Não alocado" à parte (o dinheiro é real, mas não tem
    # semana pra entrar).
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

    # Registro único de REALIZADO e PREVISTO mensal (da linha de resumo da planilha)
    if realizado_mensal > 0 or previsto_mensal > 0:
        proxy_date = _last_cargo_date or date(ano, mes_num, 28)
        records.append({
            "MES": mes_nome, "MES_NUM": mes_num, "ANO": ano, "SEMANA": "",
            "DATA": proxy_date, "DESTINO": mes_nome, "LOCAL": "", "VEICULO": "",
            "TIPO_VEICULO": "", "VALOR_FRETE": 0.0, "CLIENTE": mes_nome,
            "PREVISAO": previsto_mensal, "REALIZADO": realizado_mensal,
            "REALIZADO_DIA": 0.0, "DIFERENCA": 0.0, "OBS": "", "STATUS": "CARGO_REAL",
        })

        # Realizado por cliente direto do painel diário (soma por rótulo de
        # cliente tal como a planilha reporta) — não depende do casamento
        # carga-a-carga por data/destino, que é frágil (data mesclada, nome
        # com grafia diferente) e pode perder ou cruzar dinheiro entre
        # clientes. Confirmado batendo 100% com o resumo oficial por cliente
        # (reportado 2026-07-27).
        _painel_por_cliente: dict[str, float] = {}
        for (_d, _c), _v in day_realized.items():
            _painel_por_cliente[_c] = _painel_por_cliente.get(_c, 0.0) + _v
        for _cliente_lbl, _v_cliente in _painel_por_cliente.items():
            if _v_cliente <= 0:
                continue
            records.append({
                "MES": mes_nome, "MES_NUM": mes_num, "ANO": ano, "SEMANA": "",
                "DATA": proxy_date, "DESTINO": _cliente_lbl, "LOCAL": "", "VEICULO": "",
                "TIPO_VEICULO": "", "VALOR_FRETE": 0.0, "CLIENTE": _cliente_lbl,
                "PREVISAO": 0.0, "REALIZADO": 0.0, "REALIZADO_DIA": _v_cliente, "DIFERENCA": 0.0,
                "OBS": "Realizado oficial do painel diário, por cliente", "STATUS": "CLIENTE_REAL",
            })

    return records


def load_cargas() -> pd.DataFrame:
    """DataFrame de todas as cargas. Lê a tabela sincronizada
    `previsao_cargas` (ver `cargas/sync.py`), não mais ao vivo do Sheets."""
    return db_reader.ler_tabela("previsao_cargas")


def load_cargas_do_sheets() -> pd.DataFrame:
    """DataFrame de todas as cargas (uma aba por mês), direto do Google Sheets
    (loader original) — chamado só pelo sync (`cargas/sync.py`), nunca por uma
    view. Vazio se indisponível."""
    fonte = FONTES.get("previsao_cargas")
    if not fonte:
        return pd.DataFrame()
    sheet_id = fonte["id"]
    ttl = fonte.get("ttl", CARGAS_CACHE_TTL)

    all_records: list[dict] = []
    for mes_nome, mes_num, ano in MESES_DISPONIVEIS:
        try:
            rows = _fetch_csv(sheet_id, mes_nome, ttl)
            if not rows:
                continue
            records = _parse_month(rows, mes_nome, mes_num, ano)
            all_records.extend(records)
        except Exception as e:
            logger.warning("Não foi possível carregar %s: %s", mes_nome, str(e)[:150])

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df["DATA"] = pd.to_datetime(df["DATA"])
    df["SEMANA_ISO"] = df["DATA"].dt.isocalendar().week.astype(int)
    df["DIA_SEMANA"] = df["DATA"].dt.day_name()

    alias = {
        "NIAZITEX": "NIAZITEX",
        "NIAZITTEX": "NIAZITEX",
        "NC INDUSTRIA": "NIAZITEX",
        "BURDAYS (NAO PREVISTO)": "BURDAYS",
    }
    df["CLIENTE_NORM"] = df["CLIENTE"].apply(lambda x: alias.get(_norm(x), _norm(x)))
    df["DESTINO_NORM"] = df["DESTINO"].apply(lambda x: alias.get(_norm(x), _norm(x)))

    meses_com_real = set(df.loc[df["STATUS"] == "CARGO_REAL", "MES"].tolist())
    df["TEM_REALIZADO"] = df["MES"].isin(meses_com_real) & (df["STATUS"] != "CARGO_REAL")

    return df
