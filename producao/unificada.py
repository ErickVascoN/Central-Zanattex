"""
Camada de agrupamento/renome das facções (fatia viva, pós-cutover).

Portado da parte relevante de utils/producao_unificada.py da Central: renomeia
facções para o nome atual (ex.: Zanatta → Giattex), corrige typos de prestador,
remove prestadores inativos e agrupa os prestadores individuais sob
"QUARTERIZADAS". Assim os números batem com o "Produção por grupo" do dashboard.

(A costura com o histórico legado — planilha xlsx anterior a 01/06/2026 — entra
em um incremento posterior; hoje toda a produção atual vem das facções.)
"""

from __future__ import annotations

import pandas as pd

from integracao.fontes import FACCOES_ABAS

# Facções renomeadas pelo negócio (label interno antigo → nome atual de exibição)
FACCAO_RENOMEADA: dict[str, str] = {
    "ZANATTA":          "GIATTEX",
    "PREVITTEX FILIAL": "MEGA PREVEN MATRIZ",
    "FELIPE":           "LITEX",
}

# Typos de nome de prestador (lançamento manual) — mesma pessoa, grafias diferentes
FACCAO_TYPOS: dict[str, str] = {
    "NATHIELLY":       "NATCHIELLY",
    "PREVITEX MATRIZ": "PREVITTEX MATRIZ",
    "ELIZANGELA":      "ELISANGELA",
}

# Prestadores quarterizados que pararam de produzir — removidos do dashboard
PRESTADORES_INATIVOS: set[str] = {
    "ANGELA BANDEIRANTES",
    "ANJOS TEXTEIS",
    "DAIANE",
    "MARA",
    "MARCIA GONÇALVES",
    "MARIA GESSI",
    "MARIA HELENA",
}

GRUPO_QUARTERIZADAS = "QUARTERIZADAS"


def faccoes_fixas() -> set[str]:
    """Conjunto das facções 'fixas' (não-quarterizadas), no nome canônico atual,
    derivado de FACCOES_ABAS + FACCAO_RENOMEADA."""
    fixas = set()
    for cfg in FACCOES_ABAS.values():
        nome = cfg.get("faccao")
        if nome:
            fixas.add(FACCAO_RENOMEADA.get(nome, nome))
    return fixas


def grupo_de(faccao: str) -> str:
    """Grupo de exibição: a própria facção se fixa, senão QUARTERIZADAS."""
    return faccao if faccao in faccoes_fixas() else GRUPO_QUARTERIZADAS


def aplicar_agrupamento(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica renome/typos, remove inativos e adiciona a coluna GRUPO."""
    if df is None or df.empty:
        return df
    df = df.copy()
    df["FACCAO"] = df["FACCAO"].map(
        lambda f: FACCAO_RENOMEADA.get(FACCAO_TYPOS.get(f, f), FACCAO_TYPOS.get(f, f))
    )
    df = df[~df["FACCAO"].isin(PRESTADORES_INATIVOS)].reset_index(drop=True)
    df["GRUPO"] = df["FACCAO"].map(grupo_de)
    return df
