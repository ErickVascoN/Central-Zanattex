"""
Detecta automaticamente o GID de abas do Google Sheets.
Portado da Central (utils/gid_detector.py) — usa a biblioteca padrão (urllib).
Utilitário de apoio: use quando precisar descobrir o GID de uma aba nova.
"""

from __future__ import annotations

import re
import urllib.request
from urllib.error import HTTPError, URLError


def detectar_gid(spreadsheet_id: str) -> str:
    """Detecta o GID da primeira aba da planilha. Retorna '0' como fallback."""
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
        for pattern in (r'"gid":"?(\d+)"?', r'gid[=&](\d+)'):
            m = re.search(pattern, html)
            if m:
                return m.group(1)
    except (HTTPError, URLError):
        pass
    return "0"
