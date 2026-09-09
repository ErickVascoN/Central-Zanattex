"""
Relatório PDF da Programação de Corte — o que foi programado, o que foi
cortado e o que foi cortado fora da programação.

Layout deliberadamente enxuto: números do período, UMA tabela com a
programação (programado × cortado × diferença × status) e, embaixo, o que foi
cortado fora dela — essa em vermelho, pra saltar aos olhos. Sai em PAISAGEM,
senão a descrição do produto não cabe na mesma linha dos números.

Reusa a casca visual dos outros relatórios (`producao/relatorio_pdf.py`):
mesma faixa de marca, mesmos cards de KPI, mesma tabela navy/zebra.
"""

from __future__ import annotations

from datetime import datetime

from reportlab.lib.colors import HexColor
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from producao.relatorio_pdf import (
    LARGURA_UTIL_L, NAVY, GOOD, WARN, CRIT, FAINT, BORDER, CARD, NEUTRO_BG,
    _estilos, _faixa_marca, _titulo_secao, _bloco_kpis, _banner_meta, _tabela,
    _larguras_auto, _largura_min_coluna, _construir, _fmt, _hx,
)

LARGURA = LARGURA_UTIL_L

# Zebra e fechamento da tabela "fora da programação" (rose-50 / rose-100).
ROSA = HexColor("#fff1f2")
ROSA_FORTE = HexColor("#ffe4e6")

# Status do corte → cor (mesma leitura dos outros relatórios: verde bateu,
# âmbar parcial, vermelho não começou).
_COR_STATUS = {"Concluído": GOOD, "Parcial": WARN, "Pendente": CRIT}


def _chip(status: str, pct=None, dobro: bool = False) -> str:
    """Status + % na mesma célula, em negrito colorido — o `_tabela` renderiza
    a célula como Paragraph, então a cor tem que vir no markup.

    `dobro` acrescenta o selo "2×": o programado é o dobro do cortado e a OP
    está parada, o retrato de quantidade duplicada na origem."""
    cor = _COR_STATUS.get(status)
    txt = status or "—"
    if pct is not None:
        txt += f' <font color="#64748b">{pct:.0f}%</font>'
    corpo = txt if cor is None else f'<font color="{_hx(cor)}"><b>{txt}</b></font>'
    if dobro:
        corpo += f' <font color="{_hx(WARN)}"><b>2×</b></font>'
    return corpo


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


def _nota(texto: str, e: dict, cor=FAINT) -> Table:
    """Faixa de observação (fundo claro, filete colorido)."""
    t = Table([[Paragraph(texto, e["nota"])]], colWidths=[LARGURA])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, cor),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _tabela_alerta(cabecalho, linhas, larguras, e, aligns=None) -> Table:
    """A mesma tabela, em vermelho: o que foi cortado fora da programação tem
    que se distinguir do que estava planejado na primeira olhada. `setStyle`
    acumula comandos e os últimos vencem, então dá para repintar a tabela
    padrão sem duplicar a montagem."""
    t = _tabela(cabecalho, linhas, larguras, e, aligns=aligns)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CRIT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD, ROSA]),
        ("LINEBELOW", (0, 0), (-1, 0), 0, CRIT),
    ]))
    return t


def _subheader(texto: str, cor, e: dict) -> Table:
    """Barra de largura total abrindo um bloco de status — na cor do próprio
    status (verde cortou, âmbar parcial, vermelho não cortou), pra dar pra
    achar o bloco folheando o relatório."""
    t = Table([[Paragraph(texto, e["subnavy"])]], colWidths=[LARGURA])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), cor),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _linha_total(rotulo: str, valores: list[str], colunas: int, primeira: int) -> list:
    """Linha de fechamento da tabela (negrito, rótulo na 1ª coluna)."""
    linha = [f"<b>{rotulo}</b>"] + [""] * (colunas - 1)
    for offset, v in enumerate(valores):
        linha[primeira + offset] = f"<b>{v}</b>"
    return linha


def _valores_total(t: dict, por_semana: bool) -> list[str]:
    """Valores da linha de SUBTOTAL/TOTAL, nas mesmas colunas da tabela."""
    valores = [_fmt(t["prog"]), _fmt(t["cortado"]), _dif_txt(t["dif"])]
    if por_semana:
        valores += ["", ""]          # colunas de semana e data do corte
    return valores + [_pct_txt(t["pct"])]


def _fecha_tabela(t: Table, n_linhas: int, fundo, borda) -> Table:
    """Destaca a última linha (o TOTAL) da tabela."""
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, n_linhas), (-1, n_linhas), fundo),
        ("LINEABOVE", (0, n_linhas), (-1, n_linhas), 1, borda),
    ]))
    return t


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — PROGRAMAÇÃO DE CORTE
# ═════════════════════════════════════════════════════════════════════════════
def gerar_pdf_programacao(*, periodo_label: str, filtros: str, kpis: dict,
                          programacao: dict, fora: dict, revisar: dict,
                          dados_ate: str = "") -> bytes:
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    # "Cortes até" deixa o número auditável: a base de corte é sincronizada ao
    # longo do dia, então dois relatórios do mesmo filtro em horas diferentes
    # legitimamente divergem.
    rodape = " · ".join(x for x in [
        f"Cortes até {dados_ate}" if dados_ate else "", filtros] if x)
    story: list = [
        _faixa_marca("Programação de Corte",
                     "Programado × cortado × cortado fora da programação",
                     periodo_label, gerado_em, rodape, e, largura=LARGURA),
        Spacer(1, 0.45 * cm),
    ]

    # ── Números do período ──────────────────────────────────────────────────
    prog, cortado = kpis["total_prog_pcs"], kpis["total_cort_pcs"]
    pct_exec = round(cortado / prog * 100, 1) if prog else None
    story.append(_bloco_kpis([
        ("Programado", _fmt(prog) + " pçs"),
        ("Cortado", _fmt(cortado) + " pçs"),
        ("Diferença", _dif_txt(cortado - prog) + " pçs"),
        ("Cortado fora do plano", f"{_fmt(fora['total_pecas'])} pçs · {fora['pct']:.0f}%"),
    ], e, colunas=4, largura=LARGURA))
    story.append(Spacer(1, 0.1 * cm))
    story.append(_banner_meta(pct_exec, cortado, prog, e, largura=LARGURA))

    # ── Tabela única: a programação ─────────────────────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    story.append(_titulo_secao("Programação", e, largura=LARGURA))
    story.append(Paragraph(
        f"{_fmt(kpis['total_ops'])} OPs programadas · {kpis['concluidas']} concluídas · "
        f"{kpis['parciais']} parciais · {kpis['pendentes']} pendentes", e["sub"]))
    linhas = programacao["linhas"]
    if not linhas:
        story.append(Paragraph("Nenhum item programado no filtro atual.", e["sub"]))
    else:
        # Semana e Categoria viram coluna só quando há mais de uma no
        # resultado: filtrado a uma delas, repetiriam o mesmo valor em toda
        # linha e roubariam a largura da descrição, que é o que o corte lê.
        mostra_semana = len({l["semana"] for l in linhas}) > 1
        mostra_categoria = len({l["categoria"] for l in linhas}) > 1
        # Com filtro de semana entram as colunas que dizem quando a OP foi
        # cortada — semana para a leitura rápida ("veio da 33") e data para o
        # dia exato. São elas que explicam programado × cortado não fechar
        # quando a OP é continuação ou finalização de um corte de outra semana.
        por_semana = any(l.get("semanas_em") for l in linhas)
        # rótulos curtos: com 12 colunas os títulos longos quebram no cabeçalho
        # ("Semana/s"). A nota abaixo da tabela diz o que cada uma é.
        cols_semana = ["Sem.", "Datas do corte"] if por_semana else []
        # com ela são 11 colunas, e os rótulos longos passam a quebrar no
        # cabeçalho ("Programad/o") — encurtam nesse caso
        rot_prog, rot_dif = ("Prog.", "Dif.") if por_semana else ("Programado", "Diferença")
        cab = (["Semana"] if mostra_semana else []) + ["OP", "Cliente", "Célula"] + \
              (["Categoria"] if mostra_categoria else []) + \
              ["Produto / Descrição", rot_prog, "Cortado", rot_dif] + \
              cols_semana + ["Status"]
        exemplo = (["SEMANA 99"] if mostra_semana else []) + \
                  ["9999999999", "NIAZITEX", "GIATTEX-ZANATTA"] + \
                  (["Jogo de cama"] if mostra_categoria else []) + \
                  ["", "999.999", "999.999", "−999.999"] + \
                  (["33, 36", "01/09 a 05/09 (7 dias)"] if por_semana else []) + \
                  ["Concluído 100%"]
        i_desc = cab.index("Produto / Descrição")
        encolhiveis = tuple(i for i, c in enumerate(cab) if c in ("Semana", "Categoria"))
        larguras = _larguras(cab, exemplo, LARGURA, flex=i_desc,
                             min_flex=0.18 if por_semana else 0.30,
                             encolhiveis=encolhiveis)
        aligns = ["l"] * (i_desc + 1) + ["r", "r", "r"] + \
                 (["l", "l"] if por_semana else []) + ["l"]

        def _quando(l, campo):
            """Semana/data do corte. Só na 1ª linha da OP (o corte é lançado
            por OP); em âmbar quando nenhuma cai no período filtrado — é a OP
            que veio de continuação/finalização."""
            txt = l.get(campo)
            if not txt:
                return ""
            if l.get("corte_fora"):
                return f'<font color="{_hx(WARN)}"><b>{txt}</b></font>'
            return txt

        def _linha(l):
            return (([l["semana"]] if mostra_semana else []) +
                    [l["op"], l["cliente"], l["local"]] +
                    ([l["categoria"]] if mostra_categoria else []) +
                    [l["descricao"], _fmt(l["prog"]), _fmt(l["cortado"]),
                     _dif_txt(l["dif"])] +
                    ([_quando(l, "semanas_em"), _quando(l, "datas_em")]
                     if por_semana else []) +
                    [_chip(l["status"], l["pct"], l.get("dobro"))])

        # Um bloco por status — cortadas, parciais e não cortadas — cada um com
        # a sua barra colorida e o seu subtotal.
        for bloco in programacao.get("blocos") or []:
            if not bloco["linhas"]:
                continue
            tb = bloco["totais"]
            resumo = (bloco["rotulo"] + " · " + _fmt(bloco["itens"]) + " itens · "
                      + _fmt(tb["cortado"]) + " de " + _fmt(tb["prog"]) + " pçs")
            if tb["pct"] is not None:
                resumo += " · " + _pct_txt(tb["pct"])
            story.append(Spacer(1, 0.35 * cm))
            story.append(_subheader(resumo, _COR_STATUS.get(bloco["status"], NAVY), e))
            dados = [_linha(l) for l in bloco["linhas"]]
            dados.append(_linha_total("SUBTOTAL", _valores_total(tb, por_semana),
                                      len(cab), i_desc + 1))
            t = _tabela(cab, dados, larguras, e, aligns=aligns)
            _fecha_tabela(t, len(dados), NEUTRO_BG, NAVY)
            story.append(t)
            if bloco["ocultas"] > 0:
                story.append(Paragraph(
                    "+ " + _fmt(bloco["ocultas"]) + " itens deste bloco não couberam "
                    "no limite de linhas do relatório (o subtotal acima já os inclui).",
                    e["sub"]))

        tot = programacao.get("totais") or {}
        if tot:
            story.append(Spacer(1, 0.35 * cm))
            t = _tabela(cab, [_linha_total("TOTAL GERAL",
                                           _valores_total(tot, por_semana),
                                           len(cab), i_desc + 1)],
                        larguras, e, aligns=aligns)
            _fecha_tabela(t, 1, NEUTRO_BG, NAVY)
            story.append(t)
        if por_semana:
            story.append(Spacer(1, 0.15 * cm))
            story.append(Paragraph(
                "<b>Sem.</b> (semanas) e <b>Datas do corte</b> mostram quando a OP teve "
                "corte lançado. <font color=\"%s\"><b>Em âmbar</b></font> quando "
                "nada cai no período filtrado: a OP foi cortada antes ou depois — "
                "continuação ou finalização — e por isso programado e cortado não "
                "fecham dentro do período. São da OP inteira, então aparecem uma "
                "vez por OP." % _hx(WARN), e["sub"]))
        if programacao["truncado"]:
            story.append(Spacer(1, 0.15 * cm))
            story.append(Paragraph(
                f"Mostrando as {len(linhas)} primeiras de {_fmt(programacao['total'])} "
                f"linhas programadas — refine os filtros para ver o restante.", e["sub"]))

    # ── Aviso: programado dobrado ───────────────────────────────────────────
    suspeitas = programacao.get("suspeitas_dobro") or []
    if suspeitas:
        ops = ", ".join(f"{x['op']} ({_fmt(x['prog'])}→{_fmt(x['cortado'])})"
                        for x in suspeitas[:12])
        resto = f" e mais {_fmt(len(suspeitas) - 12)}" if len(suspeitas) > 12 else ""
        story.append(Spacer(1, 0.35 * cm))
        story.append(_nota(
            f'<font color="{_hx(WARN)}"><b>2× — possível quantidade duplicada na '
            f'origem</b></font> — em <b>{_fmt(len(suspeitas))} OPs</b> o programado é '
            f'exatamente o dobro do cortado e não há corte novo há mais de uma '
            f'semana. Costuma ser o pedido chegando com a quantidade em dobro do '
            f'sistema do cliente, não produção faltando. <b>O relatório não corrige '
            f'o número</b> — mostra o que a planilha traz: {ops}{resto}.', e, cor=WARN))

    # ── Cortado fora da programação (em vermelho) ───────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    story.append(_titulo_secao("Cortado fora da programação", e, largura=LARGURA,
                               cor=CRIT))
    story.append(Paragraph(
        "OPs que apareceram nas planilhas de corte sem constar na programação — "
        "foi cortado sem ter sido programado", e["sub"]))
    if fora.get("vazio") or not fora.get("linhas"):
        story.append(Paragraph(
            "Nenhum corte fora da programação no filtro atual.", e["sub"]))
    else:
        story.append(Spacer(1, 0.25 * cm))
        story.append(_bloco_kpis([
            ("OPs cortadas fora do plano", _fmt(fora["total_ops"])),
            ("Peças cortadas fora", _fmt(fora["total_pecas"]) + " pçs"),
            ("% de tudo que foi cortado", "%.1f%%" % fora["pct"]),
            ("Cortado sem OP informada", _fmt(fora.get("sem_op_pcs", 0)) + " pçs"),
        ], e, colunas=4, largura=LARGURA))
        cab = ["OP", "Cliente", "Célula", "Categoria", "Material", "Data(s)", "Peças"]
        exemplo = ["9999999999", "NIAZITEX", "GIATTEX", "Jogo de cama", "",
                   "01/09/2026 / 02/09/2026", "999.999"]
        larguras = _larguras(cab, exemplo, LARGURA, flex=4, min_flex=0.26,
                             encolhiveis=(3, 5))
        dados = [[
            l.get("op", "—"), (l.get("cliente") or "—"), (l.get("fonte") or "—"),
            (l.get("categoria") or "—"), (l.get("material") or "—"),
            (l.get("data") or l.get("semanas") or "—"), _fmt(l.get("qtd", 0)),
        ] for l in fora["linhas"][:60]]
        dados.append(_linha_total("TOTAL", [_fmt(fora["total_pecas"])], len(cab), 6))
        t = _tabela_alerta(cab, dados, larguras, e,
                           aligns=["l", "l", "l", "l", "l", "l", "r"])
        _fecha_tabela(t, len(dados), ROSA_FORTE, CRIT)
        story.append(t)
        notas = []
        if fora.get("sem_op_pcs"):
            notas.append("As peças cortadas sem OP informada não entram na contagem de "
                         "OPs nem no total da tabela")
        if len(fora["linhas"]) > 60:
            notas.append("mostrando as 60 maiores de " + _fmt(len(fora["linhas"])) + " OPs")
        if notas:
            story.append(Spacer(1, 0.15 * cm))
            story.append(Paragraph(" · ".join(notas), e["sub"]))

    # ── Nota: o que precisa de revisão na planilha ──────────────────────────
    if revisar.get("total"):
        itens = "; ".join(f"{l['texto']} ({l['qtd_linhas']}×, {_fmt(l['pecas'])} pçs)"
                          for l in revisar["linhas"])
        story.append(Spacer(1, 0.4 * cm))
        story.append(_nota(
            f'<font color="{_hx(WARN)}"><b>A revisar na planilha</b></font> — '
            f'<b>{_fmt(revisar["total"])} linhas</b> ({_fmt(revisar["pecas"])} pçs) '
            f'sem produto nem descrição que permita classificar: {itens}.', e, cor=WARN))

    return _construir(story, titulo=f"Programação de Corte — {periodo_label}",
                      paisagem=True)


# ═════════════════════════════════════════════════════════════════════════════
# VERSÃO IMAGEM
# ═════════════════════════════════════════════════════════════════════════════
def pdf_para_png(pdf_bytes: bytes, dpi: int = 130, max_paginas: int = 12,
                 colunas: int | None = None) -> bytes:
    """Mesmo relatório como uma imagem única, pra quem recebe no celular e não
    vai abrir PDF — WhatsApp, grupo do corte, mural.

    As páginas vão em GRADE, não empilhadas: como cada página já é paisagem,
    uma pilha vira uma tira estreita e comprida que ninguém lê. Em duas
    colunas a figura fica com proporção de folha e aproveita a largura.

    Feito só com pymupdf: monta uma página do tamanho da grade e desenha cada
    página do PDF dentro dela (`show_pdf_page`), depois rasteriza."""
    import pymupdf

    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    n = min(src.page_count, max_paginas)
    if n == 0:
        return b""
    if colunas is None:
        colunas = 1 if n < 3 else 2
    colunas = max(1, min(colunas, n))
    linhas = -(-n // colunas)          # teto da divisão

    pw, ph = src[0].rect.width, src[0].rect.height
    gap = 12  # respiro entre as páginas, pra leitura não emendar
    largura = pw * colunas + gap * (colunas - 1)
    altura = ph * linhas + gap * (linhas - 1)

    out = pymupdf.open()
    pagina = out.new_page(width=largura, height=altura)
    pagina.draw_rect(pagina.rect, color=None, fill=(1, 1, 1))
    for i in range(n):
        col, lin = i % colunas, i // colunas
        x, y = col * (pw + gap), lin * (ph + gap)
        pagina.show_pdf_page(pymupdf.Rect(x, y, x + pw, y + ph), src, i)
    return pagina.get_pixmap(dpi=dpi).tobytes("png")
