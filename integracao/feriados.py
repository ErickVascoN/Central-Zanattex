"""
Calendário de feriados (nacionais + São Paulo) para cálculo de dias úteis.
Portado da Central (utils/feriados.py) — todas as facções são de SP, então
nacional + estadual SP cobre o caso.
"""

from datetime import date, timedelta
from functools import lru_cache

import holidays as _holidays_lib
import pandas as pd


@lru_cache(maxsize=1)
def _calendario():
    return _holidays_lib.Brazil(subdiv="SP")


def _to_date(d) -> date:
    if type(d) is date:
        return d
    return pd.Timestamp(d).date()


def eh_feriado(d) -> bool:
    return _to_date(d) in _calendario()


def nome_feriado(d) -> str | None:
    return _calendario().get(_to_date(d))


def eh_dia_util(d) -> bool:
    """Dia útil = segunda a sexta e não é feriado nacional/SP."""
    dd = _to_date(d)
    return dd.weekday() < 5 and not eh_feriado(dd)


def contar_dias_uteis(ini: date, fim: date) -> int:
    if fim < ini:
        return 0
    return sum(1 for i in range((fim - ini).days + 1) if eh_dia_util(ini + timedelta(days=i)))
