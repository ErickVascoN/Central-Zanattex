"""
Relatório PDF da Programação de Corte — o que foi programado, o que foi
cortado e o que foi cortado fora da programação.

Sai em PAISAGEM (a tabela principal repete a cara da planilha de programação:
semana, OP, cliente, célula, produto, quantidade — e não cabe em retrato).
Reusa a casca visual dos outros relatórios (`producao/relatorio_pdf.py`):
mesma faixa de marca, mesmos cards de KPI, mesma tabela navy/zebra.
"""

from __future__ import annotations

from datetime import datetime

from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from producao.relatorio_pdf import (
    LARGURA_UTIL_L, GOOD, WARN, CRIT, FAINT, BORDER, CARD,
    _estilos, _faixa_marca, _titulo_secao, _bloco_kpis, _banner_meta, _tabela,
    _larguras_auto, _largura_min_coluna, _construir, _fmt, _hx, _status_pct,
)

LARGURA = LARGURA_UTIL_L

# Status do corte → cor (mesma leitura dos outros relatórios: verde bateu,
# âmbar parcial, vermelho não começou).
_COR_STATUS = {"Concluído": GOOD, "Parcial": WARN, "Pendente": CRIT}


def _chip(status: str) -> str:
    """Status em negrito colorido — o `_tabela` renderiza a célula como
    Paragraph, então a cor tem que vir no markup."""
    cor = _COR_STATUS.get(status)
    if cor is None:
        return status or "—"
    return f'<font color="{_hx(cor)}"><b>{status}</b></font>'


def _pct_txt(pct) -> str:
    return f"{pct:.0f}%" if pct is not None else "—"


def _dif_txt(dif: int) -> str:
    """Diferença cortado − programado, com sinal (o + de sobra-corte importa
    tanto quanto o − de falta)."""
    if dif == 0:
        return "0"
    return f"+{_fmt(dif)}" if dif > 0 else f"−{_fmt(abs(dif))}"


def _larguras(cabecalho: list[str], exemplos: list[str], largura: float,
              flex: int, min_flex: float = 0.0,
              encolhiveis: tuple[int, ...] = ()) -> list[float]:
    """Larguras com folga. `_larguras_auto` dá a cada coluna exatamente a
    largura do maior texto + o padding da célula — sem sobra nenhuma, um
    milésimo de arredondamento já quebra "BURDAYS" em duas linhas. Aqui cada
    coluna que não é a flexível ganha 2 caracteres de folga.

    `min_flex` (fração da largura) garante um mínimo para a coluna de texto
    livre: sem isso a descrição do produto fica com o que sobrar das outras e
    quebra em 5 linhas. O que falta é tirado proporcionalmente das colunas
    `encolhiveis` (as de texto curto, que aguentam quebrar em duas linhas)."""
    com_folga = [(c, (ex + "00") if i != flex else ex)
                 for i, (c, ex) in enumerate(zip(cabecalho, exemplos))]
    larguras = _larguras_auto(com_folga, largura, coluna_flex=flex)
    alvo = largura * min_flex
    if larguras[flex] < alvo and encolhiveis:
        # piso = o cabeçalho da coluna. Encolher além disso quebraria o título
        # ("Categori/a"), que é pior do que a descrição ocupar 2 linhas.
        pisos = {i: _largura_min_coluna(cabecalho[i]) for i in encolhiveis}
        sobra = {i: max(0.0, larguras[i] - pisos[i]) for i in encolhiveis}
        disponivel = sum(sobra.values())
        falta = min(alvo - larguras[flex], disponivel)
        if disponivel > 0 and falta > 0:
            for i in encolhiveis:
                larguras[i] -= falta * sobra[i] / disponivel
            larguras[flex] += falta
    return larguras


def _lado_a_lado(esq, dir_, e: dict, gap: float = 0.5 * cm) -> Table:
    """Dois blocos na mesma faixa — em paisagem uma tabela de 6 colunas
    sozinha deixa metade da folha vazia."""
    col = (LARGURA - gap) / 2
    t = Table([[esq, "", dir_]], colWidths=[col, gap, col])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _tabela_dimensao(titulo: str, subtitulo: str, rotulo: str, linhas: list[dict],
                     e: dict, largura: float, limite: int = 12) -> list:
    """Bloco 'programado × cortado' por semana / célula / categoria."""
    bloco = [_titulo_secao(titulo, e, largura=largura)]
    if subtitulo:
        bloco.append(Paragraph(subtitulo, e["sub"]))
    if not linhas:
        bloco.append(Paragraph("Sem dados no filtro atual.", e["sub"]))
        return bloco
    cab = [rotulo, "OPs", "Programado", "Cortado", "Dif.", "%"]
    exemplo = [max((str(l["nome"]) for l in linhas), key=len, default=""),
               "9.999", "9.999.999", "9.999.999", "−9.999.999", "9.999%"]
    larguras = _larguras(cab, exemplo, largura, flex=0)
    dados, status = [], []
    for l in linhas[:limite]:
        dados.append([l["nome"], _fmt(l["ops"]), _fmt(l["prog"]), _fmt(l["cortado"]),
                      _dif_txt(l["dif"]), _pct_txt(l["pct"])])
        status.append(_status_pct(l["pct"]))
    bloco.append(_tabela(cab, dados, larguras, e,
                         aligns=["l", "r", "r", "r", "r", "r"],
                         pct_col=5, pct_status_por_linha=status))
    if len(linhas) > limite:
        bloco.append(Spacer(1, 0.15 * cm))
        bloco.append(Paragraph(
            f"Mostrando os {limite} maiores de {len(linhas)}.", e["sub"]))
    return bloco


def _nota(texto: str, e: dict, cor=FAINT) -> Table:
    """Faixa de observação (fundo claro, filete colorido) — usada pela nota de
    itens sem categoria."""
    t = Table([[Paragraph(texto, e["nota"])]], colWidths=[LARGURA])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, cor),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — PROGRAMAÇÃO DE CORTE
# ═════════════════════════════════════════════════════════════════════════════
def gerar_pdf_programacao(*, periodo_label: str, filtros: str, kpis: dict,
                          por_semana: list[dict], por_local: list[dict],
                          por_categoria: list[dict], programacao: dict,
                          fora: dict, revisar: dict) -> bytes:
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca("Relatório de Programação de Corte",
                     "Programado × cortado — e o que foi cortado fora do plano",
                     periodo_label, gerado_em, filtros, e, largura=LARGURA),
        Spacer(1, 0.5 * cm),
    ]

    # ── Resumo geral ─────────────────────────────────────────────────────────
    prog, cortado = kpis["total_prog_pcs"], kpis["total_cort_pcs"]
    pct_exec = round(cortado / prog * 100, 1) if prog else None
    story.append(_titulo_secao("Resumo geral", e, largura=LARGURA))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis([
        ("OPs programadas", _fmt(kpis["total_ops"])),
        ("Peças programadas", _fmt(prog) + " pçs"),
        ("Peças cortadas", _fmt(cortado) + " pçs"),
        ("% do programado", _pct_txt(pct_exec)),
        ("OPs concluídas", f"{_fmt(kpis['concluidas'])} · {kpis['aderencia_pct']:.0f}%"),
        ("OPs parciais", _fmt(kpis["parciais"])),
        ("OPs pendentes", _fmt(kpis["pendentes"])),
        ("Cortado fora do plano", f"{_fmt(fora['total_pecas'])} pçs · {fora['pct']:.0f}%"),
    ], e, colunas=4, largura=LARGURA))

    # ── Cortado × Programado ─────────────────────────────────────────────────
    story.append(Spacer(1, 0.2 * cm))
    story.append(_titulo_secao("Cortado × Programado no período", e, largura=LARGURA))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_banner_meta(pct_exec, cortado, prog, e, largura=LARGURA))

    # ── Por semana ───────────────────────────────────────────────────────────
    # Com uma semana só (uso mais comum: "o relatório da semana"), essa tabela
    # repetiria o Resumo geral numa linha — o KPI acima já disse tudo.
    if len(por_semana) > 1:
        story.append(Spacer(1, 0.45 * cm))
        for bloco in _tabela_dimensao(
                "Programado × cortado por semana",
                "Cada semana da programação, com a aderência do que saiu",
                "Semana", por_semana, e, LARGURA, limite=20):
            story.append(bloco)

    # ── Por célula e por categoria (lado a lado) ─────────────────────────────
    col = (LARGURA - 0.5 * cm) / 2
    esq = _tabela_dimensao("Por célula de corte", "Onde o corte foi programado",
                           "Célula", por_local, e, col, limite=10)
    dir_ = _tabela_dimensao("Por categoria de produto", "O que foi programado",
                            "Categoria", por_categoria, e, col, limite=14)
    story.append(Spacer(1, 0.45 * cm))
    story.append(_lado_a_lado(_agrupar(esq, col), _agrupar(dir_, col), e))

    # ── Programação detalhada (a cara da planilha) ──────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    story.append(_titulo_secao("Programação detalhada", e, largura=LARGURA))
    story.append(Paragraph(
        "Item a item, na ordem da programação — com o cortado ao lado de cada "
        "linha programada", e["sub"]))
    linhas = programacao["linhas"]
    if not linhas:
        story.append(Paragraph("Nenhum item programado no filtro atual.", e["sub"]))
    else:
        # Semana e Categoria viram coluna só quando há mais de uma no
        # resultado: filtrado a uma semana/categoria elas repetiriam o mesmo
        # valor em toda linha e roubariam a largura da descrição, que é a
        # coluna que o pessoal do corte precisa ler.
        mostra_semana = len({l["semana"] for l in linhas}) > 1
        mostra_categoria = len({l["categoria"] for l in linhas}) > 1
        cab = (["Semana"] if mostra_semana else []) + ["OP", "Cliente", "Célula"] +               (["Categoria"] if mostra_categoria else []) +               ["Produto / Descrição", "Prog.", "Cortado", "Dif.", "%", "Status"]
        exemplo = (["SEMANA 99"] if mostra_semana else []) +                   ["9999999999", "NIAZITEX", "GIATTEX-ZANATTA"] +                   (["Jogo de cama"] if mostra_categoria else []) +                   ["", "999.999", "999.999", "−999.999", "999%", "Concluído"]
        i_desc = cab.index("Produto / Descrição")
        encolhiveis = tuple(i for i, c in enumerate(cab) if c in ("Semana", "Categoria"))
        larguras = _larguras(cab, exemplo, LARGURA, flex=i_desc, min_flex=0.30,
                             encolhiveis=encolhiveis)
        dados, status = [], []
        for l in linhas:
            linha = ([l["semana"]] if mostra_semana else []) +                     [l["op"], l["cliente"], l["local"]] +                     ([l["categoria"]] if mostra_categoria else []) +                     [l["descricao"], _fmt(l["prog"]), _fmt(l["cortado"]),
                     _dif_txt(l["dif"]), _pct_txt(l["pct"]), _chip(l["status"])]
            dados.append(linha)
            status.append(_status_pct(l["pct"]))
        aligns = ["l"] * (i_desc + 1) + ["r", "r", "r", "r", "l"]
        story.append(_tabela(cab, dados, larguras, e, aligns=aligns,
                             pct_col=len(cab) - 2, pct_status_por_linha=status))
        if programacao["truncado"]:
            story.append(Spacer(1, 0.15 * cm))
            story.append(Paragraph(
                f"Mostrando as {len(linhas)} primeiras de {_fmt(programacao['total'])} "
                f"linhas programadas — refine os filtros para ver o restante.", e["sub"]))

    # ── Cortado fora da programação ─────────────────────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    story.append(_titulo_secao("Cortado fora da programação", e, largura=LARGURA))
    story.append(Paragraph(
        "OPs que foram cortadas sem constar na programação da semana — produção "
        "fora do plano", e["sub"]))
    if fora.get("vazio") or not fora.get("linhas"):
        story.append(Paragraph(
            "Nenhum corte fora da programação no filtro atual.", e["sub"]))
    else:
        story.append(Spacer(1, 0.2 * cm))
        story.append(_bloco_kpis([
            ("OPs fora do plano", _fmt(fora["total_ops"])),
            ("Peças fora do plano", _fmt(fora["total_pecas"]) + " pçs"),
            ("% do corte total", f"{fora['pct']:.1f}%"),
            ("Cortado sem OP", _fmt(fora.get("sem_op_pcs", 0)) + " pçs"),
        ], e, colunas=4, largura=LARGURA))
        cab = ["OP", "Cliente", "Célula", "Categoria", "Material", "Semanas", "Peças"]
        exemplo = ["9999999", "NIAZITEX", "GIATTEX", "Jogo de cama", "", "99 / 99 / 99", "9.999.999"]
        larguras = _larguras(cab, exemplo, LARGURA, flex=4, min_flex=0.28,
                             encolhiveis=(2, 3))
        dados = [[
            l.get("op", "—"), (l.get("cliente") or "—"), (l.get("fonte") or "—"),
            (l.get("categoria") or "—"), (l.get("material") or "—"),
            (l.get("semanas") or "—"), _fmt(l.get("qtd", 0)),
        ] for l in fora["linhas"][:60]]
        story.append(_tabela(cab, dados, larguras, e,
                             aligns=["l", "l", "l", "l", "l", "l", "r"]))
        if len(fora["linhas"]) > 60:
            story.append(Spacer(1, 0.15 * cm))
            story.append(Paragraph(
                f"Mostrando as 60 maiores de {len(fora['linhas'])} OPs fora do plano.",
                e["sub"]))

    # ── Nota: o que precisa de revisão na planilha ──────────────────────────
    if revisar.get("total") or revisar.get("deduzidas"):
        story.append(Spacer(1, 0.45 * cm))
        partes = []
        if revisar.get("deduzidas"):
            partes.append(
                f"<b>{_fmt(revisar['deduzidas'])} linhas</b> sem produto na planilha "
                f"tiveram a categoria deduzida pela célula de corte.")
        if revisar.get("total"):
            itens = "; ".join(
                f"{l['texto']} ({l['qtd_linhas']}×, {_fmt(l['pecas'])} pçs)"
                for l in revisar["linhas"])
            partes.append(
                f"<b>{_fmt(revisar['total'])} linhas</b> "
                f"({_fmt(revisar['pecas'])} pçs) continuam sem categoria — a planilha "
                f"não traz produto nem descrição que permita classificar: {itens}.")
        story.append(_nota(
            '<font color="%s"><b>Itens a revisar na planilha</b></font> — %s'
            % (_hx(WARN), " ".join(partes)), e, cor=WARN))

    return _construir(story, titulo=f"Programação de Corte — {periodo_label}",
                      paisagem=True)


def _agrupar(blocos: list, largura: float):
    """Empilha uma lista de flowables numa única célula (pro lado a lado)."""
    t = Table([[b] for b in blocos], colWidths=[largura])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t
