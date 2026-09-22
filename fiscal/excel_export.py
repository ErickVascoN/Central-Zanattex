"""Exportação do relatório de Saldo Fiscal em Excel (openpyxl — já é
dependência da Central, usado em carteira/importador.py)."""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

_CABECALHO_FILL = PatternFill("solid", fgColor="172554")
_CABECALHO_FONTE = Font(color="FFFFFF", bold=True)
_COLUNAS = ["Cliente", "NF", "Produto", "NCM", "Recebido", "Unidade", "Saldo", "% Consumido"]


def gerar_xlsx_saldo(itens: list[dict]) -> bytes:
    """`itens`: mesmo formato usado em relatorio_pdf.gerar_pdf_saldo, com
    `ncm` e `unidade` adicionais."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Saldo Fiscal"

    ws.append(_COLUNAS)
    for celula in ws[1]:
        celula.fill = _CABECALHO_FILL
        celula.font = _CABECALHO_FONTE
        celula.alignment = Alignment(horizontal="center")

    for item in itens:
        ws.append([
            item["cliente"], item["nf"], item["produto"], item.get("ncm", ""),
            float(item["recebido"]), item.get("unidade", ""), float(item["saldo"]),
            round(item["pct"], 1),
        ])

    for coluna, largura in zip("ABCDEFGH", (28, 12, 40, 12, 14, 10, 14, 14)):
        ws.column_dimensions[coluna].width = largura

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
