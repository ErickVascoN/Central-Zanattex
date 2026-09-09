"""Relatório da Programação de Corte da semana — pra soltar no grupo do PCP
no início da semana, antes de qualquer corte acontecer. Por isso NÃO tem
coluna de Status/Corte (quem quiser isso usa `relatorio_pdf.py`, o relatório
completo que cruza programado × cortado). Reusa a casca visual de
`producao/relatorio_pdf.py`, mesmo padrão dos outros relatórios da Central.

Módulo separado de `relatorio_pdf.py` (que já era o nome do relatório de
programado × cortado do dashboard de análise, ver `relatorio_pdf.py`) —
mesmo nome de arquivo cobrindo dois relatórios diferentes só porque as duas
features nasceram em paralelo.

Aceita filtro por local — cada local gera seu PDF de forma independente,
nunca espera os outros ficarem prontos (ver programacao/views.py::exportar_pdf)."""
from __future__ import annotations

from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer

from producao.relatorio_pdf import (
    PAGE_W, MARGIN, _estilos, _faixa_marca, _titulo_secao, _subheader_navy,
    _bloco_kpis, _tabela, _larguras_auto, _construir, _fmt,
)

LARGURA = PAGE_W - 2 * MARGIN

_CABECALHO = ["Pedido", "Cliente", "Unidade", "Produto", "Qtde", "Destino", "Previsão"]


def gerar_pdf_programacao_semana(*, semana_label: str, periodo_label: str, gerado_em: str,
                                 gerado_por: str, grupos: list[dict],
                                 local_label: str | None = None) -> bytes:
    """`grupos`: lista de {"local_label", "itens": [{"pedido", "cliente",
    "unidade", "produto", "qtde", "destino", "previsao"}]} — um grupo por
    local, ou só um grupo quando filtrado por local específico."""
    e = _estilos()
    total_ops = sum(len(g["itens"]) for g in grupos)
    total_pecas = sum(item["qtde"] for g in grupos for item in g["itens"])
    clientes = {item["cliente"] for g in grupos for item in g["itens"]}

    titulo = (f"Programação de Corte — {local_label}" if local_label
             else "Programação de Corte")
    subtitulo = ("O que foi programado para entrar no corte" if not local_label
                else f"O que foi programado para {local_label}")

    story: list = [
        _faixa_marca(titulo, subtitulo, periodo_label, gerado_em,
                     f"Gerado por {gerado_por}", e),
        Spacer(1, 0.5 * cm),
        _titulo_secao("Resumo da semana", e),
        Spacer(1, 0.3 * cm),
        _bloco_kpis([
            ("OPs programadas", str(total_ops)),
            ("Peças programadas", _fmt(total_pecas)),
            ("Clientes atendidos", str(len(clientes))),
            ("Locais envolvidos", str(len(grupos))),
        ], e, colunas=4),
        Spacer(1, 0.3 * cm),
    ]

    if not grupos:
        story.append(Paragraph("Nenhuma OP programada nesse filtro.", e["nota"]))
        return _construir(story, titulo)

    story.append(_titulo_secao("Detalhamento por local", e))
    larguras = _larguras_auto(
        [("Pedido", "PC-888888"), ("Cliente", "Mega Preven Matriz"),
         ("Unidade", "Manta Iacanga"), ("Produto", "Manta Casal Dupla Face"),
         ("Qtde", "0.000"), ("Destino", "Mega Preven Matriz"), ("Previsão", "00/00")],
        LARGURA, coluna_flex=3,
    )
    for grupo in grupos:
        soma = sum(item["qtde"] for item in grupo["itens"])
        story.append(Spacer(1, 0.25 * cm))
        story.append(_subheader_navy(
            f"{grupo['local_label'].upper()} — {_fmt(soma)} pçs ({len(grupo['itens'])} OPs)", e))
        linhas = [
            [item["pedido"], item["cliente"], item["unidade"], item["produto"],
             _fmt(item["qtde"]), item["destino"], item["previsao"]]
            for item in grupo["itens"]
        ]
        story.append(_tabela(_CABECALHO, linhas, larguras, e, aligns=["l", "l", "l", "l", "r", "l", "l"]))

    return _construir(story, titulo)
