"""
Premiação por Produtividade (ANEXO I) — GGTTEX Jogos e Fronha.

Classifica cada lançamento de produção interna (colunas já normalizadas por
`interno_loader.load_interno_unidade`) em uma atividade do Anexo e aplica a
`RegraPremiacao` vigente na data. As regras (meta e R$/peça) ficam no banco
(admin), não aqui — só o mapeamento planilha → atividade é fixo em código.

O bônus é calculado por PERÍODO FECHADO, não dia a dia: a meta do período é
"dias úteis do período × meta diária" (dias úteis fixos do calendário,
independente de falta/ausência) e só o que passar dessa meta total no
período gera bônus — dias fracos e fortes se compensam dentro do período.
Para as atividades com meta por tamanho (Costura de Canto/Elástico), como a
meta diária varia, "meta diária" do período é a média das metas dos dias em
que a pessoa produziu (ponderada pelos tamanhos realmente feitos).
"""

from __future__ import annotations

import pandas as pd

from integracao.normalize import normalize_text
from .models import RegraPremiacao

UNIDADES_PREMIACAO = ("GGTTEX_JOGOS", "GGTTEX_FRONHA")

Atividade = RegraPremiacao.Atividade

_TAMANHOS_VALIDOS = ("SOLTEIRO", "CASAL", "QUEEN")

# GGTTEX_JOGOS: SETOR → atividade (direto, sem depender de PRODUTO/DESCRIÇÃO)
_ATIVIDADES_JOGOS_SETOR = {
    "COSTURA CANTO": Atividade.COSTURA_CANTO,
    "COSTURA GALONEIRA": Atividade.COSTURA_ELASTICO,
    "COSTURA RETA": Atividade.COSTURA_RETA,
    "COSTUR RETA": Atividade.COSTURA_RETA,  # erro de digitação comum na planilha
    # "Bainha" é tarefa de Fronha — aparecer no setor de Jogos é lançamento
    # cruzado (planilha errada), mas conta como Bainha ou Fechamento mesmo assim.
    "BAINHA DE FRONHA": Atividade.BAINHA_FECHAMENTO,
    "BAINHA FRONHA": Atividade.BAINHA_FECHAMENTO,
}
# GGTTEX_JOGOS: SETOR "MESA" + PRODUTO (descrição) → atividade (meta fixa,
# sem tamanho registrado na planilha)
_ATIVIDADES_JOGOS_MESA = {
    "DOBRA FUNDO": Atividade.DOBRA_FUNDO,
    "DOBRA E EMPAPELA": Atividade.DOBRA_EMPAPELA,
    "CASEADO": Atividade.CASEADO,
    "EMBALAGEM": Atividade.EMBALAGEM,
}
# GGTTEX_FRONHA: FUNCAO → atividade. Bainha e Fechamento contam para o mesmo
# balde (Anexo dá uma única meta/taxa para "Bainha ou Fechamento").
_ATIVIDADES_FRONHA = {
    "BAINHA": Atividade.BAINHA_FECHAMENTO,
    "FECHA": Atividade.BAINHA_FECHAMENTO,
    "ESTICA": Atividade.ESTICADO,
    "DESVIRA": Atividade.DESVIRADO,
}

_COLUNAS_CALC = ["COLABORADOR", "ATIVIDADE", "PRODUZIDO", "DIAS_TRABALHADOS",
                  "META_MEDIA_DIA", "META_TOTAL", "EXCEDENTE", "VALOR_BONUS"]
_COLUNAS_DETALHE = ["COLABORADOR", "DATA", "ATIVIDADE", "QUANTIDADE",
                     "META_DIA", "TAMANHO_USADO"]


def _status_pct(pct):
    if pct is None:
        return "neutro"
    if pct >= 100:
        return "good"
    if pct >= 80:
        return "warn"
    return "crit"


def classificar_atividade(df: pd.DataFrame, unidade: str) -> pd.DataFrame:
    """Adiciona a coluna ATIVIDADE (código de RegraPremiacao.Atividade) a cada
    linha. Linhas não reconhecidas (defeito, troca de etiqueta etc.) ficam com
    ATIVIDADE=''."""
    out = df.copy()
    if out.empty:
        out["ATIVIDADE"] = ""
        return out

    if unidade == "GGTTEX_JOGOS":
        setor_n = out["SETOR"].map(normalize_text)
        produto_n = out["PRODUTO"].map(normalize_text)

        def _classificar(setor, produto):
            if setor in _ATIVIDADES_JOGOS_SETOR:
                return _ATIVIDADES_JOGOS_SETOR[setor]
            if setor == "MESA":
                return _ATIVIDADES_JOGOS_MESA.get(produto, "")
            return ""

        out["ATIVIDADE"] = [
            _classificar(s, p) for s, p in zip(setor_n, produto_n)
        ]
    elif unidade == "GGTTEX_FRONHA":
        funcao_n = out["FUNCAO"].map(normalize_text)
        out["ATIVIDADE"] = funcao_n.map(lambda f: _ATIVIDADES_FRONHA.get(f, ""))
    else:
        out["ATIVIDADE"] = ""
    return out


def _regras_por_atividade(unidade: str) -> dict[str, list[RegraPremiacao]]:
    """RegraPremiacao da unidade, agrupadas por atividade e ordenadas da mais
    recente para a mais antiga (para achar a vigente numa data)."""
    agrupado: dict[str, list[RegraPremiacao]] = {}
    qs = RegraPremiacao.objects.filter(unidade=unidade).order_by("-vigente_desde")
    for r in qs:
        agrupado.setdefault(r.atividade, []).append(r)
    return agrupado


def _regra_na_data(regras: list[RegraPremiacao], data) -> RegraPremiacao | None:
    for r in regras:
        if r.vigente_desde <= data and (r.vigente_ate is None or r.vigente_ate >= data):
            return r
    return None


def _meta_do_dia(regra: RegraPremiacao, grupo_dia: pd.DataFrame) -> tuple[int, str]:
    """Meta aplicável a um (colaborador, atividade, dia) e o "tamanho usado"
    (rótulo p/ exibição). Se a atividade não usa tamanho, é sempre a meta
    padrão da regra."""
    if not regra.usa_tamanho:
        return int(regra.meta_padrao or 0), ""

    tamanhos_serie = grupo_dia.get("TAMANHO", pd.Series(dtype=str)).map(normalize_text)
    tamanhos_validos = set(t for t in tamanhos_serie if t in _TAMANHOS_VALIDOS)
    tem_desconhecido = bool(set(tamanhos_serie) - tamanhos_validos - {""}) or \
        (tamanhos_serie.eq("").any() and not tamanhos_validos)

    if len(tamanhos_validos) == 1 and not tem_desconhecido:
        tamanho = next(iter(tamanhos_validos))
        meta = {"SOLTEIRO": regra.meta_solteiro, "CASAL": regra.meta_casal,
                "QUEEN": regra.meta_queen}[tamanho]
        return int(meta or 0), tamanho.title()

    return int(regra.meta_tamanhos_mistos or 0), "Tamanhos mistos"


def _dias_do_periodo(df_periodo: pd.DataFrame, unidade: str):
    """Para cada (COLABORADOR, ATIVIDADE), gera a lista de dias trabalhados
    com (quantidade do dia, meta do dia, regra usada naquele dia)."""
    df = classificar_atividade(df_periodo, unidade)
    df = df[df["ATIVIDADE"] != ""].copy()
    if df.empty:
        return df

    df["DIA"] = df["DATA"].dt.normalize()
    regras_por_atividade = _regras_por_atividade(unidade)

    linhas = []
    for (colab, dia, atividade), grupo_dia in df.groupby(
            ["COLABORADOR", "DIA", "ATIVIDADE"], sort=False):
        quantidade = int(grupo_dia["QUANTIDADE"].sum())
        if quantidade <= 0:
            continue
        regras = regras_por_atividade.get(atividade)
        regra = _regra_na_data(regras, dia.date()) if regras else None
        if regra is None:
            continue
        meta_dia, tamanho_usado = _meta_do_dia(regra, grupo_dia)
        linhas.append({
            "COLABORADOR": colab, "DATA": dia, "ATIVIDADE": atividade,
            "QUANTIDADE": quantidade, "META_DIA": meta_dia,
            "TAMANHO_USADO": tamanho_usado, "_REGRA": regra,
        })
    return pd.DataFrame(linhas)


def calcular_premiacao(df_periodo: pd.DataFrame, unidade: str,
                        dias_uteis_periodo: int) -> pd.DataFrame:
    """Fecha o período por (COLABORADOR, ATIVIDADE): meta do período = média
    das metas diárias dos dias trabalhados × dias úteis do período; excedente
    e bônus são calculados uma única vez sobre o total do período (não por
    dia) — produção acima da meta em um dia compensa produção abaixo em
    outro, dentro do mesmo período."""
    if df_periodo is None or df_periodo.empty:
        return pd.DataFrame(columns=_COLUNAS_CALC)

    dias = _dias_do_periodo(df_periodo, unidade)
    if dias.empty:
        return pd.DataFrame(columns=_COLUNAS_CALC)

    linhas = []
    for (colab, atividade), grupo in dias.groupby(
            ["COLABORADOR", "ATIVIDADE"], sort=False):
        produzido = int(grupo["QUANTIDADE"].sum())
        dias_trabalhados = len(grupo)
        media_meta_dia = grupo["META_DIA"].mean()
        meta_total = int(round(media_meta_dia * dias_uteis_periodo))
        excedente = max(0, produzido - meta_total)
        regra_ref = grupo.sort_values("DATA").iloc[-1]["_REGRA"]
        valor_bonus = round(excedente * float(regra_ref.valor_peca_excedente), 2)

        linhas.append({
            "COLABORADOR": colab, "ATIVIDADE": atividade,
            "PRODUZIDO": produzido, "DIAS_TRABALHADOS": dias_trabalhados,
            "META_MEDIA_DIA": round(media_meta_dia, 1), "META_TOTAL": meta_total,
            "EXCEDENTE": excedente, "VALOR_BONUS": valor_bonus,
        })

    return pd.DataFrame(linhas, columns=_COLUNAS_CALC)


def detalhe_diario(df_periodo: pd.DataFrame, unidade: str) -> pd.DataFrame:
    """Produção dia a dia por atividade (informativo — sem bônus, já que o
    bônus só existe fechado no período). Usado no relatório de 1 colaborador
    para mostrar o ritmo diário sem sugerir que cada dia paga isoladamente."""
    dias = _dias_do_periodo(df_periodo, unidade)
    if dias.empty:
        return pd.DataFrame(columns=_COLUNAS_DETALHE)
    return dias[_COLUNAS_DETALHE].sort_values(["COLABORADOR", "DATA"]).reset_index(drop=True)


def resumo_por_colaborador(df_calc: pd.DataFrame) -> list[dict]:
    """Agrega por colaborador (produzido, meta, %, excedente, valor de
    bonificação do período fechado), com detalhamento por atividade."""
    if df_calc is None or df_calc.empty:
        return []

    rotulos = dict(Atividade.choices)
    out = []
    for colab, grupo in df_calc.groupby("COLABORADOR", sort=False):
        produzido = int(grupo["PRODUZIDO"].sum())
        meta = int(grupo["META_TOTAL"].sum())
        excedente = int(grupo["EXCEDENTE"].sum())
        valor = round(float(grupo["VALOR_BONUS"].sum()), 2)
        pct = round(produzido / meta * 100, 1) if meta else None

        atividades = []
        for _, r in grupo.sort_values("PRODUZIDO", ascending=False).iterrows():
            a_produzido = int(r["PRODUZIDO"])
            a_meta = int(r["META_TOTAL"])
            atividades.append({
                "atividade": rotulos.get(r["ATIVIDADE"], r["ATIVIDADE"]),
                "produzido": a_produzido,
                "meta": a_meta,
                "pct": round(a_produzido / a_meta * 100, 1) if a_meta else None,
                "excedente": int(r["EXCEDENTE"]),
                "valor": round(float(r["VALOR_BONUS"]), 2),
            })

        out.append({
            "nome": str(colab).title(),
            "produzido": produzido,
            "meta": meta,
            "pct": pct,
            "status": _status_pct(pct),
            "excedente": excedente,
            "valor": valor,
            "atividades": atividades,
        })
    out.sort(key=lambda c: c["valor"], reverse=True)
    return out


def totais_premiacao(resumo: list[dict]) -> dict:
    return {
        "colaboradores": len(resumo),
        "produzido": sum(c["produzido"] for c in resumo),
        "meta": sum(c["meta"] for c in resumo),
        "excedente": sum(c["excedente"] for c in resumo),
        "valor": round(sum(c["valor"] for c in resumo), 2),
    }
