"""
Registro central das fontes de dados (planilhas Google Sheets).

Portado de `config/settings.py` da Central — apenas os IDs/GIDs/TTLs das
planilhas (as senhas internas NÃO foram trazidas; a autenticação agora é o
login Django). Cada dashboard, ao ser portado nas próximas fases, referencia
estas fontes em vez de espalhar IDs pelo código.

Estrutura de cada fonte:
  chave -> { "id", "gid", "ttl", "label", "date_order" (opcional) }

Os TTLs replicam a frescura de hoje (Corte 60s; Produção 120s; demais 300s),
para que preencher a planilha continue atualizando o dashboard como sempre.
"""

from __future__ import annotations

# TTLs (segundos) — mesma frescura da Central atual
TTL_CORTE = 60
TTL_PRODUCAO = 120
TTL_PADRAO = 300

# ── Fontes principais (uma aba/GID por fonte) ────────────────────────────────
FONTES: dict[str, dict] = {
    "corte_arealva": {
        "id": "1KLbNpw-P28YgoijXfMXU-zRQULuDHMMB",
        "gid": "1544210185",
        "ttl": TTL_CORTE,
        "label": "Corte · Arealva (Mantas)",
    },
    "corte_iacanga": {
        "id": "1FBpCrq29_e1UBNwBlcgPTz66tbpUsgcgtzfXi4DcORU",
        "gid": "0",
        "ttl": TTL_CORTE,
        "label": "Corte · Iacanga (Mantas Giattex)",
    },
    "faturamento": {
        "id": "1tpQmqkinlA4AscPI8kIkmm5DGD9Jw_wHb-5sy5itSGg",
        "gid": "1255712550",
        "ttl": TTL_PADRAO,
        "label": "Faturamento (Produtos Faturados)",
    },
    "producao_geral": {
        "id": "15s_ZttYG4UkSprgp4V_9gUBSgg7p8JRTiSQZL4xBi6Y",
        "gid": "0",
        "ttl": TTL_PRODUCAO,
        "label": "Produção Geral",
    },
    "carteira": {
        "id": "1U-iNIQRqKOIBrDZ86ZE5uJW6IQCzugJ7",
        "gid": "611396912",
        "ttl": TTL_PADRAO,
        "label": "Carteira de Pedidos",
    },
}

# ── Previsão de Cargas — uma aba por mês (ex.: "JUNHO", "JULHO"), sem GID fixo ─
# Buscada por NOME da aba (get_raw_sheet), não por GID — por isso fica fora de
# FONTES (endereçada por gid) e de todas_as_fontes().
CARGAS_SHEET_ID = "1RvC2dkk9KCribduCoxXM6sKGB0lxuIXk"
CARGAS_TTL = TTL_PADRAO

# ── Produção interna por colaborador (uma aba/GID por unidade) ────────────────
# Planilhas com título mesclado no cabeçalho, datas M/D/YYYY e quantidade com
# ponto de milhar — o loader de produção (Fase 3) detecta colunas por substring.
PRODUCAO_INTERNO: dict[str, dict] = {
    "LITTEX":         {"id": "1wpCdsgLVv_R14yDkak6OMwXKJjUbvL9p", "gid": "1697720285", "label": "LITTEX",         "date_order": "MDY"},
    "GGTTEX_JOGOS":   {"id": "1b8gCNUqZagkINAN1egnA7Va6g6Bv4esv", "gid": "410924690",  "label": "GGTTEX Jogos",   "date_order": "MDY"},
    "GGTTEX_FRONHA":  {"id": "1b8gCNUqZagkINAN1egnA7Va6g6Bv4esv", "gid": "671875370",  "label": "GGTTEX Fronha",  "date_order": "MDY"},
    "GGTTEX_CORTINA": {"id": "1PG5t_aWif2iJiCyEtgKE6sLFvMu7w5sL", "gid": "296216772",  "label": "GGTTEX Cortina", "date_order": "MDY"},
}

# ── Produção externa (facções) — uma planilha, várias abas por GID ────────────
FACCOES_SHEET_ID = "1V05lVI-HlZXpGTc1p3R2V7ddnMTTjSOQ"
FACCOES_TTL = TTL_PADRAO
# aba -> {gid, faccao (None = usa o prestador), por_prestador?}
FACCOES_ABAS: dict[str, dict] = {
    "QUARTERIZADAS":    {"gid": "994268246",  "faccao": None, "por_prestador": True},
    "GGTTEX (RUTE)":    {"gid": "1265193869", "faccao": "GGTTEX RUTE"},
    "GGTTEX (CORTINA)": {"gid": "1766002384", "faccao": "GGTTEX CORTINA"},
    "ZANATTA":          {"gid": "670406828",  "faccao": "ZANATTA"},
    "PREVITTEX MATRIZ": {"gid": "1938192189", "faccao": "PREVITTEX MATRIZ"},
    "PREVITTEX FILIAL": {"gid": "1921426222", "faccao": "PREVITTEX FILIAL"},
    "MEGA BARIRI":      {"gid": "1219460477", "faccao": "MEGA BARIRI"},
    "MEGA PREVEN (BOCA)": {"gid": "431490653", "faccao": "MEGA PREVEN (BOCA)"},
}


# ── Aliases de produto/cliente e cores (portados de config/settings.py) ───────
# Unificam grafias diferentes do mesmo produto/cliente entre as abas de facção.
FACCOES_PRODUTO_ALIAS: dict[str, str] = {
    "OUTLET PRENSADO":       "MANTA PRENSADA",
    "OUTLET C\\CINTA":       "MANTA C/CINTA",
    "FRONHA":                "FRONHA AVULSA",
    "FRONHAS":               "FRONHA AVULSA",
    "FRONHAS PONTO PALITO":  "FRONHA PONTO PALITO",
    "BABY":                  "COBERTOR BABY",
    "VELOUR":                "COBERTOR VELOUR",
    "MICRO 180G":            "COBERTOR 180G",
    "MANTA MAGICA":          "MICRO 180G (MAGICA)",
    "JOGO":                  "JOGOS DUPLOS",
    "JOGO DUPLO":            "JOGOS DUPLOS",
    "JOGO DE CAMA":          "JOGOS DUPLOS",
    "JOGOS DE CAMA":         "JOGOS DUPLOS",
    "JOGO CAMA":             "JOGOS DUPLOS",
    "JOGO SIMPLES":          "JOGOS SIMPLES",
    "JG DUPLO PONTO PALITO": "JG PONTO PALITO",
    "LENCOL QE":             "LENCOL AVULSO",
    "LENCOL ST":             "LENCOL AVULSO",
    "LENCOL CS":             "LENCOL AVULSO",
    "LENCOL KING":           "LENCOL AVULSO",
}

FACCOES_CLIENTE_ALIAS: dict[str, str] = {
    "NC INDUSTRIA": "NIAZITTEX",  # NC Indústria = Niazittex = Niazi (mesma empresa)
}

# Guia de metas dentro da planilha de facções (fonte primária das metas)
FACCOES_GID_METAS = "1797767576"
METAS_TTL = 3600  # 1 hora

# Alias de facções/prestadores: nome na guia de metas → nome canônico da produção.
# Casa os nomes da guia de metas com os nomes usados na produção (inclui renomes).
FACCOES_FACCAO_ALIAS: dict[str, str] = {
    "NATHIELLY":        "NATCHIELLY",
    "LITTEX":           "LITEX",
    "CORTINA (GGTTEX)": "GGTTEX CORTINA",
    "RUTE (ZANATTEX)":  "GGTTEX RUTE",
    "RUTE ZANATTEX":    "GGTTEX RUTE",
    "MEGA FILIAL":      "MEGA PREVEN FILIAL",
    "BOCA":             "MEGA PREVEN (BOCA)",
    "MEGA (BOCA)":      "MEGA PREVEN (BOCA)",
    "PREVITTEX":        "PREVITTEX MATRIZ",
    "GGTTEX (RUTE)":    "GGTTEX RUTE",
    "GGTTEX (CORTINA)": "GGTTEX CORTINA",
    "LETICIA (GIATTEX)": "LETICIA",
    "ZANATTA":          "GIATTEX",
    "PREVITTEX FILIAL": "MEGA PREVEN MATRIZ",
    "GIATEX":           "GIATTEX",
    "GISELE (IACANGA)": "GIATTEX",
}

# Cores por facção (paleta usada nos gráficos)
CORES_FACCAO: dict[str, str] = {
    "GIATTEX":          "#4ECDC4",
    "GGTTEX RUTE":      "#45B7D1",
    "GGTTEX CORTINA":   "#5DA9E9",
    "PREVITTEX MATRIZ": "#FFA726",
    "MEGA BARIRI":      "#FF6B6B",
    "MEGA PREVEN":      "#AB47BC",
    "LITEX":            "#34D399",
    "QUARTERIZADAS":    "#94A3B8",
}


def todas_as_fontes() -> dict[str, dict]:
    """Achata FONTES + PRODUCAO_INTERNO + facções num único dicionário para
    diagnóstico (comando checar_fontes)."""
    combinado: dict[str, dict] = {}
    for chave, f in FONTES.items():
        combinado[chave] = f
    for chave, f in PRODUCAO_INTERNO.items():
        combinado[f"producao_interno::{chave}"] = {
            "id": f["id"], "gid": f["gid"], "ttl": TTL_PADRAO,
            "label": f"Produção Interna · {f['label']}",
        }
    for aba, f in FACCOES_ABAS.items():
        combinado[f"faccoes::{aba}"] = {
            "id": FACCOES_SHEET_ID, "gid": f["gid"], "ttl": FACCOES_TTL,
            "label": f"Facções · {aba}",
        }
    return combinado
