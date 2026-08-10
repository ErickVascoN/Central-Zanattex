"""Filtros conexos (estilo slicer do Excel / Power BI) para os dropdowns do
topo dos dashboards.

O problema que isto resolve: até então cada dropdown listava *todos* os valores
da base, então marcar uma Categoria não reduzia a lista de Produtos — o usuário
tinha que caçar, entre 387 produtos, os poucos daquela categoria.

Como funciona: monta-se uma "matriz" com as combinações distintas de valores
que realmente existem na base (uma linha por combinação, valores trocados por
índices). Com ela dá pra responder, para cada filtro, quais valores continuam
possíveis *dados os outros filtros já marcados* — sem nunca restringir o filtro
por ele mesmo (senão marcar uma opção sumiria com as demais do próprio menu).

A mesma matriz é enviada ao navegador (`matriz` no contexto), onde o JS de
`base.html` repete exatamente esta conta a cada clique — daí os menus se
ajustarem na hora, antes mesmo de "Aplicar". O cálculo aqui no servidor é o que
garante que a página já chegue coerente (e que funcione sem JS).
"""

from __future__ import annotations

import pandas as pd


# Marcas de "não preenchido / não se aplica" que as planilhas usam. Nenhuma
# vira opção de filtro: um checkbox "-" (ou em branco) não diz nada a ninguém.
# Só somem da LISTA — as linhas continuam contando em todos os totais.
_PLACEHOLDERS = {"", "-", "--", "---", "—", "...", "..", ".",
                 "NAN", "NAT", "NONE", "NULL", "N/A", "NA", "N/D", "#N/D", "N/I"}


def eh_placeholder(v) -> bool:
    return str(v).strip().upper() in _PLACEHOLDERS


def contar_distintos(serie, excluir=()) -> int:
    """Quantos valores distintos de verdade. Marcador de ausência não é mais
    um produto/cor/cliente — célula em branco ou "-" não vira "+1 produto".
    `excluir`: rótulos próprios do módulo (ex.: "SEM OP" no Corte)."""
    s = serie.dropna().astype(str)
    return int(s[~s.isin(tuple(excluir)) & ~s.map(eh_placeholder)].nunique())


def _ordenar(valores: list[str], ordem: str) -> list[str]:
    if ordem == "int":
        return sorted(valores, key=lambda v: int(v))
    if ordem == "original":
        return valores          # ordem de aparição na base (ex.: meses de Cargas)
    return sorted(valores)


def _vocabulario(df: pd.DataFrame, campo: dict) -> list[str]:
    """Todos os valores oferecidos no dropdown, já ordenados. Marcador de
    ausência (em branco, "-", "NAN"…) nunca vira opção — ver `_PLACEHOLDERS`."""
    if campo.get("valores") is not None:
        return [str(v) for v in campo["valores"] if not eh_placeholder(v)]
    col = campo["col"]
    if col not in df.columns:
        return []
    vistos, ordem_original = set(), []
    for v in df[col].tolist():
        s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
        if not eh_placeholder(s) and s not in vistos:
            vistos.add(s)
            ordem_original.append(s)
    excluir = set(campo.get("excluir") or ())
    return _ordenar([v for v in ordem_original if v not in excluir],
                    campo.get("ordem", "sorted"))


def _linhas(df: pd.DataFrame, campos: list[dict], vocab: dict[str, list[str]]) -> list[list[int]]:
    """Combinações distintas existentes na base, com os valores trocados pelo
    índice no vocabulário do filtro (-1 = valor que não é opção, ex.: "N/I" —
    a linha existe, mas nunca casa com uma seleção daquele filtro)."""
    indices = {c["name"]: {v: i for i, v in enumerate(vocab[c["name"]])} for c in campos}
    colunas = []
    for c in campos:
        col, nome = c.get("col"), c["name"]
        if not col or col not in df.columns:
            colunas.append(None)
        else:
            colunas.append(df[col].map(lambda v, n=nome: indices[n].get(
                "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v), -1)))
    if not any(s is not None for s in colunas):
        return []
    n = len(df)
    tabela = pd.DataFrame({
        c["name"]: (s if s is not None else pd.Series([-1] * n, index=df.index))
        for c, s in zip(campos, colunas)
    })
    return tabela.drop_duplicates().astype(int).values.tolist()


def _compativeis(nomes: list[str], linhas: list[list[int]],
                 sel_idx: dict[str, set[int]]) -> dict[str, set[int]]:
    """Para cada filtro, os índices de valores que sobrevivem aos *outros*
    filtros marcados. Uma linha que só destoa num único filtro ainda oferece o
    valor dela naquele filtro — é isso que permite marcar uma segunda opção do
    mesmo menu (marcar "Lençol" não pode sumir com "Cortina" do menu Categoria).
    """
    ativos = [i for i, n in enumerate(nomes) if sel_idx.get(n)]
    compat: dict[str, set[int]] = {n: set() for n in nomes}
    for linha in linhas:
        falhas = [i for i in ativos if linha[i] not in sel_idx[nomes[i]]]
        if not falhas:                       # linha casa com tudo que está marcado
            for i, nome in enumerate(nomes):
                if linha[i] >= 0:
                    compat[nome].add(linha[i])
        elif len(falhas) == 1:               # casaria se este filtro fosse relaxado
            i = falhas[0]
            if linha[i] >= 0:
                compat[nomes[i]].add(linha[i])
    return compat


def preparar(df: pd.DataFrame, campos: list[dict], sel: dict[str, list]) -> dict:
    """Monta os dropdowns conexos de um dashboard.

    `campos`: um dict por filtro, na ordem em que aparecem na toolbar:
        name     nome do parâmetro na querystring (ex.: "produtos")
        label    rótulo exibido (ex.: "Produto")
        col      coluna do DataFrame com o valor (ex.: "SUBCATEGORIA")
        valores  (opcional) lista fixa de opções, no lugar de ler de `col`
        excluir  (opcional) valores que não viram opção (ex.: {"N/I"})
        ordem    (opcional) "sorted" (padrão) · "int" · "original"
        rotulos  (opcional) {valor: rótulo} para exibição (ex.: meses)
        titulo   (opcional) exibir o rótulo em Title Case
    `sel`: {name: [valores marcados]} — cru da querystring, sem validar.

    Devolve `{"filtros": [...], "opcoes": {...}, "matriz": {...}}`:
        filtros  lista pro template (via `_partials/filtros_dropdowns.html`)
        opcoes   {name: [valores]} — para a view validar a seleção
        matriz   payload que o JS usa pra refazer a conta a cada clique
    """
    nomes = [c["name"] for c in campos]
    vocab = {c["name"]: _vocabulario(df, c) for c in campos}
    linhas = _linhas(df, campos, vocab)

    indices = {n: {v: i for i, v in enumerate(vocab[n])} for n in nomes}
    sel_valores = {n: [str(v) for v in (sel.get(n) or [])] for n in nomes}
    sel_idx = {n: {indices[n][v] for v in sel_valores[n] if v in indices[n]} for n in nomes}
    sel_idx = {n: s for n, s in sel_idx.items() if s}

    compat = _compativeis(nomes, linhas, sel_idx)
    # opção que não aparece em nenhuma combinação (ex.: valor que só existe em
    # linhas sintéticas de Cargas) não tem como ser cruzada — sem evidência,
    # não esconde.
    presentes = {n: {linha[i] for linha in linhas} for i, n in enumerate(nomes)}

    filtros = []
    for campo in campos:
        nome = campo["name"]
        rotulos = campo.get("rotulos") or {}
        marcados = set(sel_valores[nome])
        opcoes = []
        for i, valor in enumerate(vocab[nome]):
            selecionado = valor in marcados
            opcoes.append({
                "valor": valor,
                "label": rotulos.get(valor, valor),
                "selecionado": selecionado,
                # o que está marcado nunca some — senão não teria como desmarcar
                "ok": selecionado or i in compat[nome] or i not in presentes[nome],
            })
        filtros.append({
            "label": campo["label"], "name": nome, "opcoes": opcoes,
            "titulo": bool(campo.get("titulo")),
            "n_sel": sum(1 for o in opcoes if o["selecionado"]),
            "n_ok": sum(1 for o in opcoes if o["ok"]),
        })

    return {
        "filtros": filtros,
        "opcoes": vocab,
        "matriz": {"campos": nomes, "vocab": vocab, "linhas": linhas},
    }
