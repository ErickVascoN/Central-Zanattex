"""
Serviço de dados do dashboard "Plano de Metas".

Previsto vem da BD Plano de Metas (aba nova na planilha de Produção de
Facções, sincronizada em `plano_metas`). Realizado é calculado
AUTOMATICAMENTE a partir da produção real já sincronizada
(`producao_faccoes` + unidade interna LITTEX em `producao_interno`) — não do
lançamento manual da planilha (`STATUS = REALIZADO` na BD), que o usuário não
confia 100%. O lançamento manual continua guardado só pra comparação, na
seção de Divergências.

Só a meta "PRODUTO ACABADO" é comparável automaticamente — a produção real
sincronizada só tem produto FINAL, não os processos intermediários
("PRODUTO SEMIACABADO": bainha, dobra, costura etc., > metade da base,
exclusivo das Quarterizadas). Pra semiacabado não existe fonte de conferência
automática hoje — fica numa seção separada, só informativa, sem comparação
(mostrar 0% ali seria um falso alarme, não um dado real).

Desenho pensado pra nunca "esconder" produção real de ninguém: o Realizado
por prestador/cliente/produto vem de agregados independentes da produção
real (não de uma junção linha-a-linha frágil por produto exato) — só o
detalhe de Divergências por produto específico depende de casar o nome do
produto, e mesmo aí com aproximação por nome (`metas/matching.py`).
"""

from __future__ import annotations

import pandas as pd

from integracao import db_reader
from integracao.normalize import normalize_text

from .matching import LITTEX_SENTINEL, resolver_cliente, resolver_produto, resolver_responsavel

_ATIVIDADE_COMPARAVEL = "PRODUTO ACABADO"


def _status(pct):
    if pct is None:
        return "neutro"
    if pct >= 100:
        return "good"
    if pct >= 80:
        return "warn"
    return "crit"


def carregar_plano_metas_bruto() -> pd.DataFrame:
    """Todas as linhas sincronizadas (PREVISTO + REALIZADO manual, Acabado +
    Semiacabado). Lê a tabela `plano_metas` (ver `metas/sync.py`)."""
    return db_reader.ler_tabela("plano_metas")


def meses_disponiveis(df: pd.DataFrame) -> list[pd.Timestamp]:
    if df.empty:
        return []
    return sorted(df["MES"].dropna().unique(), reverse=True)


def _universo_nomes_producao() -> tuple[set[str], set[str], set[str]]:
    """Nomes (FACCAO, PRODUTO, CLIENTE) conhecidos em TODA a produção real
    sincronizada, sem filtrar por mês — usado só pra reconhecer o nome
    (existe ou não), nunca pra somar quantidade. Importante pra não confundir
    "nome desconhecido" com "conhecido, mas zero neste mês específico"
    (ex.: um prestador que não produziu nada num mês, mas produziu em
    outros — precisa continuar reconhecível, senão vira falso positivo na
    lista de divergências)."""
    faccoes_nomes, produtos_nomes, clientes_nomes = set(), set(), set()

    faccoes = db_reader.ler_tabela("producao_faccoes")
    if faccoes is not None and not faccoes.empty:
        faccoes_nomes |= set(faccoes["FACCAO"].unique())
        produtos_nomes |= set(faccoes["PRODUTO"].unique())
        clientes_nomes |= set(faccoes["CLIENTE"].unique())

    interno = db_reader.ler_tabela("producao_interno")
    if interno is not None and not interno.empty:
        i = interno[interno["UNIDADE"] == "LITTEX"]
        if not i.empty:
            produtos_nomes |= set(i["PRODUTO"].unique())
            if "CLIENTE" in i.columns:
                clientes_nomes |= set(i["CLIENTE"].unique())

    faccoes_nomes |= {LITTEX_SENTINEL}
    return faccoes_nomes, produtos_nomes, clientes_nomes


def _producao_real_do_mes(mes: pd.Timestamp) -> pd.DataFrame:
    """Produção real do mês, granular (FACCAO, PRODUTO, CLIENTE, QUANTIDADE)
    — junta producao_faccoes com a unidade interna LITTEX
    (producao_interno). Só cobre produto ACABADO (mesma natureza dos dois)."""
    partes = []

    faccoes = db_reader.ler_tabela("producao_faccoes")
    if faccoes is not None and not faccoes.empty:
        f = faccoes[(faccoes["DATA"].dt.year == mes.year) & (faccoes["DATA"].dt.month == mes.month)]
        if not f.empty:
            partes.append(f[["FACCAO", "PRODUTO", "CLIENTE", "QUANTIDADE"]])

    interno = db_reader.ler_tabela("producao_interno")
    if interno is not None and not interno.empty:
        i = interno[
            (interno["UNIDADE"] == "LITTEX")
            & (interno["DATA"].dt.year == mes.year) & (interno["DATA"].dt.month == mes.month)
        ].copy()
        if not i.empty:
            i["FACCAO"] = LITTEX_SENTINEL
            if "CLIENTE" not in i.columns:
                i["CLIENTE"] = ""
            partes.append(i[["FACCAO", "PRODUTO", "CLIENTE", "QUANTIDADE"]])

    if not partes:
        return pd.DataFrame(columns=["FACCAO", "PRODUTO", "CLIENTE", "QUANTIDADE"])
    return pd.concat(partes, ignore_index=True)


def cruzar_mes(df_bruto: pd.DataFrame, mes: pd.Timestamp) -> dict:
    """Retorna um dict com:
      - 'acabado': linhas de Previsto (Produto Acabado) + RESPONSAVEL_RESOLVIDO
        + REALIZADO_PRESTADOR (total real do prestador no mês, mesmo valor
        repetido pra cada produto — pra somar direito ver `resumo_geral`/
        `por_dimensao`, que já tratam isso) + REALIZADO_MANUAL (lançado à
        mão na BD, pra comparação).
      - 'semiacabado': linhas de Previsto (Produto Semiacabado) — sem
        verificação automática, informativo.
      - 'realizado_prestador' / 'realizado_produto' / 'realizado_cliente':
        totais agregados da produção real do mês, independentes entre si.
    """
    previsto_mes = df_bruto[(df_bruto["STATUS"] == "PREVISTO") & (df_bruto["MES"] == mes)]
    acabado = previsto_mes[previsto_mes["ATIVIDADE"] == _ATIVIDADE_COMPARAVEL].copy()
    semiacabado = previsto_mes[previsto_mes["ATIVIDADE"] != _ATIVIDADE_COMPARAVEL].copy()

    real = _producao_real_do_mes(mes)
    faccoes_conhecidas, produtos_conhecidos, clientes_conhecidos = _universo_nomes_producao()

    realizado_prestador = real.groupby("FACCAO")["QUANTIDADE"].sum().to_dict()
    realizado_produto = real.groupby("PRODUTO")["QUANTIDADE"].sum().to_dict()
    cliente_real = real[real["CLIENTE"].astype(str).str.strip() != ""]
    realizado_cliente = cliente_real.groupby("CLIENTE")["QUANTIDADE"].sum().to_dict()

    if acabado.empty:
        acabado = acabado.assign(RESPONSAVEL_RESOLVIDO=None, REALIZADO_PRESTADOR=0, REALIZADO_MANUAL=0)
    else:
        manual = df_bruto[(df_bruto["STATUS"] == "REALIZADO") & (df_bruto["MES"] == mes)]
        manual_por_resp = manual.groupby("RESPONSAVEL")["QUANTIDADE"].sum().to_dict()

        acabado["RESPONSAVEL_RESOLVIDO"] = acabado.apply(
            lambda r: resolver_responsavel(r["RESPONSAVEL"], r["PRODUTO"], faccoes_conhecidas), axis=1,
        )
        acabado["REALIZADO_PRESTADOR"] = (
            acabado["RESPONSAVEL_RESOLVIDO"].map(realizado_prestador).fillna(0).astype(int)
        )
        acabado["REALIZADO_MANUAL"] = acabado["RESPONSAVEL"].map(manual_por_resp).fillna(0).astype(int)

    return {
        "acabado": acabado,
        "semiacabado": semiacabado,
        "realizado_prestador": realizado_prestador,
        "realizado_produto": realizado_produto,
        "produtos_reais": produtos_conhecidos,
        "realizado_cliente": realizado_cliente,
        "clientes_reais": clientes_conhecidos,
        "real": real,
    }


def resumo_geral(cruzado: dict) -> dict:
    """Previsto x Realizado agregados (só Produto Acabado — a parte
    comparável). Realizado conta o total de cada prestador UMA VEZ (não por
    produto), pra não inflar somando o mesmo total repetido."""
    acabado = cruzado["acabado"]
    if acabado.empty:
        return {"previsto": 0, "realizado": 0, "pct": None, "saldo": 0}
    previsto = int(acabado["QUANTIDADE"].sum())
    resolvidos = acabado["RESPONSAVEL_RESOLVIDO"].dropna().unique()
    realizado = int(sum(cruzado["realizado_prestador"].get(r, 0) for r in resolvidos))
    pct = round(realizado / previsto * 100, 1) if previsto else None
    return {"previsto": previsto, "realizado": realizado, "pct": pct, "saldo": realizado - previsto}


def por_dimensao(cruzado: dict, dimensao: str) -> list[dict]:
    """dimensao em {'cliente', 'prestador', 'produto'}. Realizado vem do
    agregado independente daquela dimensão (não da junção por produto
    exato), pra nunca perder produção real por causa de outra coluna não
    bater."""
    acabado = cruzado["acabado"]
    if acabado.empty:
        return []

    if dimensao == "prestador":
        g = acabado.groupby("RESPONSAVEL").agg(
            PREVISTO=("QUANTIDADE", "sum"), REALIZADO=("REALIZADO_PRESTADOR", "first"),
        ).reset_index().rename(columns={"RESPONSAVEL": "NOME"})
    elif dimensao == "produto":
        realizado_produto = cruzado["realizado_produto"]
        produtos_reais = cruzado["produtos_reais"]
        g = acabado.groupby("PRODUTO")["QUANTIDADE"].sum().reset_index().rename(
            columns={"PRODUTO": "NOME", "QUANTIDADE": "PREVISTO"})
        g["REALIZADO"] = g["NOME"].apply(
            lambda p: realizado_produto.get(resolver_produto(p, produtos_reais), 0)
        )
    elif dimensao == "cliente":
        realizado_cliente = cruzado["realizado_cliente"]
        clientes_reais = cruzado["clientes_reais"]
        g = acabado.groupby("CLIENTE")["QUANTIDADE"].sum().reset_index().rename(
            columns={"CLIENTE": "NOME", "QUANTIDADE": "PREVISTO"})
        g["REALIZADO"] = g["NOME"].apply(
            lambda c: realizado_cliente.get(resolver_cliente(c, clientes_reais), 0)
        )
    else:
        raise ValueError(f"dimensao inválida: {dimensao!r}")

    g = g[(g["PREVISTO"] > 0) | (g["REALIZADO"] > 0)]
    linhas = []
    for _, r in g.iterrows():
        previsto, realizado = int(r["PREVISTO"]), int(r["REALIZADO"])
        pct = round(realizado / previsto * 100, 1) if previsto > 0 else None
        linhas.append({
            "nome": str(r["NOME"]).title(),
            "previsto": previsto, "realizado": realizado, "pct": pct,
            "saldo": realizado - previsto, "status": _status(pct),
        })
    linhas.sort(key=lambda x: x["previsto"], reverse=True)
    return linhas


def _top(df: pd.DataFrame, col: str, limite: int = 6) -> list[tuple[str, int]]:
    """Top N valores de `col` por QUANTIDADE, desc. [(nome, qtd), ...]."""
    if df.empty:
        return []
    s = df.groupby(col)["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False).head(limite)
    if col == "FACCAO":
        return [("Litex" if k == LITTEX_SENTINEL else str(k).title(), int(v)) for k, v in s.items()]
    return [(str(k).title(), int(v)) for k, v in s.items()]


def detalhe_dimensao(cruzado: dict, dimensao: str) -> dict[str, dict[str, list]]:
    """Pra cada valor do ranking de `dimensao`, quebra o Realizado pelas
    outras duas dimensões (quem produziu / qual produto / qual cliente) —
    usa o MESMO nome resolvido que calculou o número do ranking, pra sempre
    bater com o que está no gráfico. Serve de "flag" no hover: mostra de
    onde vem o Realizado, o que expõe na hora quando ele está inflado por
    produção de outro prestador/produto (ex.: nome genérico casando com uma
    variante mais específica)."""
    acabado = cruzado["acabado"]
    real = cruzado["real"]
    if acabado.empty or real.empty:
        return {}

    out: dict[str, dict[str, list]] = {}

    if dimensao == "prestador":
        for _, r in acabado.drop_duplicates("RESPONSAVEL").iterrows():
            resolvido = r["RESPONSAVEL_RESOLVIDO"]
            nome = str(r["RESPONSAVEL"]).title()
            if pd.isna(resolvido):
                out[nome] = {"produto": [], "cliente": []}
                continue
            sub = real[real["FACCAO"] == resolvido]
            out[nome] = {"produto": _top(sub, "PRODUTO"), "cliente": _top(sub, "CLIENTE")}

    elif dimensao == "produto":
        produtos_reais = cruzado["produtos_reais"]
        for produto in acabado["PRODUTO"].dropna().unique():
            nome = str(produto).title()
            resolvido = resolver_produto(produto, produtos_reais)
            if resolvido is None:
                out[nome] = {"prestador": [], "cliente": []}
                continue
            sub = real[real["PRODUTO"] == resolvido]
            out[nome] = {"prestador": _top(sub, "FACCAO"), "cliente": _top(sub, "CLIENTE")}

    elif dimensao == "cliente":
        clientes_reais = cruzado["clientes_reais"]
        for cliente in acabado["CLIENTE"].dropna().unique():
            nome = str(cliente).title()
            resolvido = resolver_cliente(cliente, clientes_reais)
            if resolvido is None:
                out[nome] = {"prestador": [], "produto": []}
                continue
            sub = real[real["CLIENTE"] == resolvido]
            out[nome] = {"prestador": _top(sub, "FACCAO"), "produto": _top(sub, "PRODUTO")}

    return out


def top_desvios(cruzado: dict, dimensao: str, n: int = 10) -> list[dict]:
    linhas = [l for l in por_dimensao(cruzado, dimensao) if l["pct"] is not None]
    linhas.sort(key=lambda x: x["pct"])
    return linhas[:n]


def divergencias(cruzado: dict) -> dict:
    """Três listas:
      - sem_match: responsável do plano sem correspondência confiável na
        produção real (nome não reconhecido — revisar/cadastrar alias).
      - nao_lancado: prestador com produção real MAIOR que o total lançado
        manualmente na BD (provável esquecimento no lançamento).
      - semiacabado: metas de processo (bainha, dobra, costura...) sem
        fonte automática de conferência hoje — só informativo.
    """
    acabado = cruzado["acabado"]
    if acabado.empty:
        sem_match, nao_lancado = [], []
    else:
        sem_match_df = acabado[acabado["RESPONSAVEL_RESOLVIDO"].isna() & (acabado["QUANTIDADE"] > 0)]
        sem_match = sorted({
            (r["RESPONSAVEL"].title(), r["PRODUTO"].title()) for _, r in sem_match_df.iterrows()
        })

        por_prestador = acabado.groupby("RESPONSAVEL").agg(
            REALIZADO=("REALIZADO_PRESTADOR", "first"), MANUAL=("REALIZADO_MANUAL", "first"),
        ).reset_index()
        faltando = por_prestador[por_prestador["REALIZADO"] > por_prestador["MANUAL"]]
        nao_lancado = [
            {
                "responsavel": r["RESPONSAVEL"].title(),
                "realizado_automatico": int(r["REALIZADO"]),
                "realizado_manual": int(r["MANUAL"]),
                "diferenca": int(r["REALIZADO"] - r["MANUAL"]),
            }
            for _, r in faltando.iterrows()
        ]
        nao_lancado.sort(key=lambda x: x["diferenca"], reverse=True)

    semi = cruzado["semiacabado"]
    semi_lista = []
    if not semi.empty:
        g = semi.groupby(["RESPONSAVEL", "PRODUTO"])["QUANTIDADE"].sum().reset_index()
        g = g[g["QUANTIDADE"] > 0].sort_values("QUANTIDADE", ascending=False)
        semi_lista = [
            {"responsavel": r["RESPONSAVEL"].title(), "produto": r["PRODUTO"].title(),
             "previsto": int(r["QUANTIDADE"])}
            for _, r in g.iterrows()
        ]

    return {
        "sem_match": [{"responsavel": r, "produto": p} for r, p in sem_match],
        "nao_lancado": [d for d in nao_lancado if d["diferenca"] > 0],
        "semiacabado": semi_lista,
    }


def evolucao_mensal(df_bruto: pd.DataFrame) -> dict:
    """Previsto x Realizado automático por mês (só Produto Acabado) — só
    faz sentido com mais de 1 mês de dado (mesma regra do resto do app)."""
    meses = meses_disponiveis(df_bruto)
    if len(meses) < 2:
        return {"x": [], "previsto": [], "realizado": []}
    linhas = []
    for mes in sorted(meses):
        cruzado = cruzar_mes(df_bruto, mes)
        resumo = resumo_geral(cruzado)
        linhas.append((mes, resumo["previsto"], resumo["realizado"]))
    return {
        "x": [m.strftime("%b/%Y") for m, _, _ in linhas],
        "previsto": [p for _, p, _ in linhas],
        "realizado": [r for _, _, r in linhas],
    }
