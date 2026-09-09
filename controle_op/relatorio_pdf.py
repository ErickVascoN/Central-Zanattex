"""PDF de fechamento de uma OP — cabeçalho + resumo de aproveitamento +
histórico completo de registros de corte. Reusa os helpers de
producao/relatorio_pdf.py, mesmo padrão de corte/relatorio_pdf.py e
programacao/relatorio_pdf.py."""
from __future__ import annotations

from datetime import date

from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer

from contas.models import UnidadeCorte
from producao.relatorio_pdf import (
    PAGE_W, MARGIN, _estilos, _faixa_marca, _titulo_secao, _banner_meta,
    _tabela, _larguras_auto, _construir, _fmt,
)

LARGURA = PAGE_W - 2 * MARGIN

_UNIDADES_MANTA = {UnidadeCorte.AREALVA_MANTA, UnidadeCorte.IACANGA_MANTA}
_UNIDADES_COM_RETALHO = {UnidadeCorte.AREALVA_MANTA, UnidadeCorte.IACANGA_MANTA, UnidadeCorte.LENCOL}


def gerar_pdf_fechamento(*, programacao, aproveitamento, registros: list,
                          producao=None, envios: list | None = None, retornos: list | None = None) -> bytes:
    e = _estilos()
    gerado_em = date.today().strftime("%d/%m/%Y")
    pedido = programacao.pedido or programacao.op_interna
    titulo = f"Fechamento de OP — {pedido}"

    story: list = [
        _faixa_marca(titulo, programacao.cliente, programacao.semana, gerado_em,
                     f"{programacao.get_local_display()} · Destino: {programacao.destino_costura}", e),
        Spacer(1, 0.5 * cm),
        _titulo_secao("Resumo", e),
        Spacer(1, 0.2 * cm),
    ]

    pct = round((aproveitamento.pct_pecas or 0) * 100, 1)
    story.append(_banner_meta(pct, aproveitamento.cortado_pecas, programacao.qnt_programada, e))

    if aproveitamento.retalho_pct is not None:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"<b>% de retalho:</b> {aproveitamento.retalho_pct * 100:.1f}%", e["nota"]))

    if aproveitamento.kg_final is not None:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"<b>Kg final (peças + retalho + baby):</b> {aproveitamento.kg_final:.2f} kg · "
            f"<b>Divergência:</b> {aproveitamento.divergencia_kg:.2f} kg · "
            f"<b>Aproveitamento (peso):</b> {aproveitamento.aproveitamento_peso_pct * 100:.1f}%",
            e["nota"]))

    if aproveitamento.metros_programado is not None or aproveitamento.metros_cortado_real is not None:
        programado = f"{aproveitamento.metros_programado:.1f} m" if aproveitamento.metros_programado is not None else "—"
        cortado_real = f"{aproveitamento.metros_cortado_real:.1f} m" if aproveitamento.metros_cortado_real is not None else "—"
        texto = f"<b>Programado (rolo):</b> {programado} · <b>Cortado (real):</b> {cortado_real}"
        if aproveitamento.aproveitamento_metros_pct is not None:
            texto += f" · <b>Aproveitamento:</b> {aproveitamento.aproveitamento_metros_pct * 100:.1f}%"
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(texto, e["nota"]))

    if aproveitamento.eh_jogo_de_cama:
        fundo = f"{aproveitamento.fundo_cortado} pçs" if aproveitamento.fundo_cortado is not None else "—"
        fronha = f"{aproveitamento.fronha_cortada} pçs" if aproveitamento.fronha_cortada is not None else "—"
        texto = (f"<b>Caseamento — Lençol de cima:</b> {aproveitamento.cortado_pecas} pçs · "
                 f"<b>Fundo:</b> {fundo} · <b>Fronha:</b> {fronha}")
        if aproveitamento.casados is not None:
            texto += (f" · <b>Caseados:</b> {aproveitamento.casados} · "
                      f"<b>Avulsos:</b> cima {aproveitamento.avulsos_cima}")
            if aproveitamento.avulsos_fundo is not None:
                texto += f" / fundo {aproveitamento.avulsos_fundo}"
            if aproveitamento.avulsos_fronha is not None:
                texto += f" / fronha {aproveitamento.avulsos_fronha}"
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(texto, e["nota"]))

    story.append(Spacer(1, 0.4 * cm))
    story.append(_titulo_secao("Produto", e))
    story.append(Paragraph(
        f"{programacao.produto} — {programacao.tamanho or 'sem tamanho informado'}", e["nota"]))

    story.append(Spacer(1, 0.4 * cm))
    story.append(_titulo_secao("Histórico de registros", e))
    if not registros:
        story.append(Paragraph("Nenhum registro de corte lançado ainda.", e["nota"]))
    else:
        # Colunas variam por unidade — Manta pesa (kg), Lençol mede
        # (metros), nenhuma das duas aparece pra Cortina/Itaju (só
        # peça/peça, sem kg nem metros na fonte real dessas unidades).
        unidade = programacao.unidade_corte
        colunas = [("Data", "00/00/0000"), ("Peças", "0.000")]
        if unidade in _UNIDADES_MANTA:
            colunas.append(("Kg", "0.00"))
        if unidade == UnidadeCorte.LENCOL:
            colunas.append(("Metros", "0.00"))
        if unidade in _UNIDADES_COM_RETALHO:
            colunas.append(("Retalho (kg)", "0.00"))
        colunas.append(("Lançado por", "usuario.teste"))

        larguras = _larguras_auto(colunas, LARGURA, coluna_flex=len(colunas) - 1)
        cabecalho = [c[0] for c in colunas]

        linhas = []
        for r in registros:
            linha = [r.data.strftime("%d/%m/%Y"), _fmt(r.quantidade_pecas)]
            if unidade in _UNIDADES_MANTA:
                linha.append(f"{r.kg_cortado:.2f}" if r.kg_cortado is not None else "—")
            if unidade == UnidadeCorte.LENCOL:
                linha.append(f"{r.metros_cortado:.2f}" if r.metros_cortado is not None else "—")
            if unidade in _UNIDADES_COM_RETALHO:
                linha.append(f"{r.retalho_kg:.2f}" if r.retalho_kg is not None else "—")
            linha.append(r.criado_por.get_username())
            linhas.append(linha)

        aligns = ["l", "r"] + ["r"] * (len(colunas) - 3) + ["l"]
        story.append(_tabela(cabecalho, linhas, larguras, e, aligns=aligns))

    if producao is not None:
        story.append(Spacer(1, 0.4 * cm))
        story.append(_titulo_secao("Produção — envio e retorno", e))
        texto = (f"<b>Enviado:</b> {producao.enviado_pecas} pçs · "
                 f"<b>Retornado:</b> {producao.retornado_pecas} pçs · "
                 f"<b>Saldo na indústria:</b> {producao.saldo_industria} pçs · "
                 f"<b>Status:</b> {producao.status_label}")
        if producao.retalho_producao_kg is not None:
            texto += f" · <b>Retalho de produção:</b> {producao.retalho_producao_kg:.2f} kg"
        story.append(Paragraph(texto, e["nota"]))

        if envios:
            story.append(Spacer(1, 0.3 * cm))
            cabecalho = ["Data", "Destino", "Peças enviadas"]
            larguras = _larguras_auto(
                [("Data", "00/00/0000"), ("Destino", "Facção Exemplo Ltda"), ("Peças enviadas", "0.000")],
                LARGURA, coluna_flex=1)
            linhas = [[ev.data.strftime("%d/%m/%Y"), ev.destino, _fmt(ev.quantidade_pecas)] for ev in envios]
            story.append(_tabela(cabecalho, linhas, larguras, e, aligns=["l", "l", "r"]))

        if retornos:
            story.append(Spacer(1, 0.3 * cm))
            cabecalho = ["Data", "Peças retornadas", "Retalho (kg)"]
            larguras = _larguras_auto(
                [("Data", "00/00/0000"), ("Peças retornadas", "0.000"), ("Retalho (kg)", "0.00")],
                LARGURA, coluna_flex=0)
            linhas = [
                [rt.data.strftime("%d/%m/%Y"), _fmt(rt.quantidade_pecas),
                 f"{rt.retalho_kg:.2f}" if rt.retalho_kg is not None else "—"]
                for rt in retornos
            ]
            story.append(_tabela(cabecalho, linhas, larguras, e, aligns=["l", "r", "r"]))

    return _construir(story, titulo)
