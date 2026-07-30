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

SÁBADO fica fora dessa conta: como a meta do período só cobre dias úteis,
misturar o sábado inflava o excedente. A produção de sábado é somada à parte
e paga integralmente pelo mesmo R$/peça da atividade (sem meta, sem
excedente) — inclusive para quem ficou abaixo da meta nos dias úteis.
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
                  "META_MEDIA_DIA", "META_TOTAL", "EXCEDENTE", "VALOR_BONUS",
                  "PRODUZIDO_SABADO", "DIAS_SABADO", "VALOR_SABADO"]
_COLUNAS_DETALHE = ["COLABORADOR", "DATA", "ATIVIDADE", "QUANTIDADE",
                     "META_DIA", "TAMANHO_USADO", "OBSERVACAO"]


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


def _dias_do_periodo(df_periodo: pd.DataFrame, unidade: str, incluir_sem_producao: bool = False):
    """Para cada (COLABORADOR, ATIVIDADE), gera a lista de dias trabalhados
    com (quantidade do dia, meta do dia, regra usada naquele dia).

    `incluir_sem_producao=True` também mantém dias com QUANTIDADE=0 (falta,
    revisão de carga, sábado etc.) — usado só pelo "ritmo diário" informativo
    (`detalhe_diario`). `calcular_premiacao` chama sem esse parâmetro (padrão
    False), então o fechamento do bônus continua ignorando dias zerados como
    sempre — nada muda no cálculo."""
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
        if quantidade <= 0 and not incluir_sem_producao:
            continue
        regras = regras_por_atividade.get(atividade)
        regra = _regra_na_data(regras, dia.date()) if regras else None
        if regra is None:
            continue
        meta_dia, tamanho_usado = _meta_do_dia(regra, grupo_dia)
        obs_serie = grupo_dia.get("OBSERVACAO", pd.Series(dtype=str))
        observacao = next((str(v) for v in obs_serie if str(v).strip()), "")
        linhas.append({
            "COLABORADOR": colab, "DATA": dia, "ATIVIDADE": atividade,
            "QUANTIDADE": quantidade, "META_DIA": meta_dia,
            "TAMANHO_USADO": tamanho_usado, "OBSERVACAO": observacao, "_REGRA": regra,
        })
    return pd.DataFrame(linhas)


def calcular_premiacao(df_periodo: pd.DataFrame, unidade: str,
                        dias_uteis_periodo: int) -> pd.DataFrame:
    """Fecha o período por (COLABORADOR, ATIVIDADE): meta do período = média
    das metas diárias dos dias trabalhados × dias úteis do período; excedente
    e bônus são calculados uma única vez sobre o total do período (não por
    dia) — produção acima da meta em um dia compensa produção abaixo em
    outro, dentro do mesmo período.

    Quem faz mais de uma função divide os MESMOS dias entre elas, então a meta
    do período é rateada pela fatia de dias de cada atividade — senão cada uma
    levava o período inteiro e a meta virava 2-3x a real (ver `peso` abaixo).
    Para quem faz uma função só o peso é 1 e nada muda."""
    if df_periodo is None or df_periodo.empty:
        return pd.DataFrame(columns=_COLUNAS_CALC)

    dias = _dias_do_periodo(df_periodo, unidade)
    if dias.empty:
        return pd.DataFrame(columns=_COLUNAS_CALC)

    # Total de dias úteis lançados por colaborador, somando todas as atividades
    # — denominador do rateio da meta. Conta linhas (colab, dia, atividade), e
    # não datas distintas, pra que os pesos das atividades somem exatamente 1
    # mesmo quando a pessoa faz duas funções no mesmo dia.
    eh_fds_todos = dias["DATA"].dt.weekday >= 5
    dias_uteis_colab = dias[~eh_fds_todos].groupby("COLABORADOR").size().to_dict()

    linhas = []
    for (colab, atividade), grupo in dias.groupby(
            ["COLABORADOR", "ATIVIDADE"], sort=False):
        # Sábado/domingo saem da conta da meta: a meta do período conta só dias
        # úteis do calendário, então misturar os dois inflava o excedente (quem
        # trabalhava sábado ganhava excedente sem meta correspondente).
        eh_fds = grupo["DATA"].dt.weekday >= 5
        g_uteis, g_sabado = grupo[~eh_fds], grupo[eh_fds]

        produzido = int(g_uteis["QUANTIDADE"].sum())
        dias_trabalhados = len(g_uteis)
        # Fatia do período que cabe a esta atividade. Sem nenhum dia útil no
        # período (só sábado), não há meta a cumprir — peso 0.
        total_dias_colab = dias_uteis_colab.get(colab, 0)
        peso = dias_trabalhados / total_dias_colab if total_dias_colab else 0.0
        # Se só houve sábado no período, usa as metas do próprio sábado como
        # referência (evita média vazia = NaN).
        base_meta = g_uteis if not g_uteis.empty else grupo
        media_meta_dia = base_meta["META_DIA"].mean()
        meta_total = int(round(media_meta_dia * dias_uteis_periodo * peso))
        excedente = max(0, produzido - meta_total)
        regra_ref = grupo.sort_values("DATA").iloc[-1]["_REGRA"]
        valor_peca = float(regra_ref.valor_peca_excedente)
        valor_bonus = round(excedente * valor_peca, 2)

        # Sábado: paga o mesmo R$/peça sobre TUDO que foi produzido, já que não
        # há meta no sábado. É independente do excedente dos dias úteis — quem
        # ficou abaixo da meta na semana recebe pelo sábado do mesmo jeito.
        produzido_sabado = int(g_sabado["QUANTIDADE"].sum())
        valor_sabado = round(produzido_sabado * valor_peca, 2)

        linhas.append({
            "COLABORADOR": colab, "ATIVIDADE": atividade,
            "PRODUZIDO": produzido, "DIAS_TRABALHADOS": dias_trabalhados,
            "META_MEDIA_DIA": round(media_meta_dia, 1), "META_TOTAL": meta_total,
            "EXCEDENTE": excedente, "VALOR_BONUS": valor_bonus,
            "PRODUZIDO_SABADO": produzido_sabado, "DIAS_SABADO": len(g_sabado),
            "VALOR_SABADO": valor_sabado,
        })

    return pd.DataFrame(linhas, columns=_COLUNAS_CALC)


def detalhe_diario(df_periodo: pd.DataFrame, unidade: str) -> pd.DataFrame:
    """Produção dia a dia por atividade (informativo — sem bônus, já que o
    bônus só existe fechado no período). Usado no relatório de 1 colaborador
    para mostrar o ritmo diário sem sugerir que cada dia paga isoladamente.

    Inclui dias com QUANTIDADE=0 (falta, revisão de carga, sábado etc.) junto
    com a OBSERVACAO da planilha — dá visibilidade da assiduidade pra análise,
    sem entrar no cálculo do bônus (que usa `_dias_do_periodo` sem esse
    parâmetro, em `calcular_premiacao`)."""
    dias = _dias_do_periodo(df_periodo, unidade, incluir_sem_producao=True)
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
        produzido_sabado = int(grupo["PRODUZIDO_SABADO"].sum())
        valor_sabado = round(float(grupo["VALOR_SABADO"].sum()), 2)

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
                "produzido_sabado": int(r["PRODUZIDO_SABADO"]),
                "valor_sabado": round(float(r["VALOR_SABADO"]), 2),
            })

        out.append({
            "nome": str(colab).title(),
            "produzido": produzido,
            "meta": meta,
            "pct": pct,
            "status": _status_pct(pct),
            "excedente": excedente,
            "valor": valor,
            "produzido_sabado": produzido_sabado,
            "valor_sabado": valor_sabado,
            "valor_total": round(valor + valor_sabado, 2),
            "atividades": atividades,
        })
    out.sort(key=lambda c: c["valor_total"], reverse=True)
    return out


def totais_premiacao(resumo: list[dict]) -> dict:
    valor = round(sum(c["valor"] for c in resumo), 2)
    valor_sabado = round(sum(c["valor_sabado"] for c in resumo), 2)
    return {
        "colaboradores": len(resumo),
        "produzido": sum(c["produzido"] for c in resumo),
        "meta": sum(c["meta"] for c in resumo),
        "excedente": sum(c["excedente"] for c in resumo),
        "valor": valor,
        "produzido_sabado": sum(c["produzido_sabado"] for c in resumo),
        "valor_sabado": valor_sabado,
        "valor_total": round(valor + valor_sabado, 2),
    }
