"""
Diagnóstico do cálculo de metas (Realizado × Meta) por facção.
Confere os números contra o dashboard atual antes de montar a UI.

Uso:
    python manage.py checar_metas --mes 7 --ano 2026
"""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand

from producao.servicos import carregar_producao
from producao.metas_calc import calcular_meta_faccoes
from producao.metas import load_metas


def _fmt(v) -> str:
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


class Command(BaseCommand):
    help = "Calcula meta por facção (Realizado x Meta) e imprime o ranking."

    def add_arguments(self, parser):
        hoje = date.today()
        parser.add_argument("--mes", type=int, default=hoje.month)
        parser.add_argument("--ano", type=int, default=hoje.year)

    def handle(self, *args, **opts):
        mes, ano = opts["mes"], opts["ano"]

        metas = load_metas()
        self.stdout.write(self.style.MIGRATE_HEADING(f"Metas cadastradas na guia: {len(metas)}"))

        df = carregar_producao()
        if df.empty:
            self.stderr.write(self.style.ERROR("Sem dados de produção."))
            return

        df_p = df[(df["Ano"] == ano) & (df["Mes"] == mes)]
        if df_p.empty:
            self.stdout.write(self.style.WARNING(f"Nenhuma produção em {mes:02d}/{ano}."))
            return

        res = calcular_meta_faccoes(
            df_p[["DATA", "FACCAO", "PRODUTO", "CLIENTE", "QUANTIDADE"]], ano, mes
        )
        rank = res["rank_df"]

        total = res["total_geral"]
        meta_total = res["meta_mes_total"]
        pct_total = round(total / meta_total * 100, 1) if meta_total else None

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nRealizado x Meta | {mes:02d}/{ano}"))
        self.stdout.write(self.style.SUCCESS(
            f"  TOTAL: {_fmt(total)} / meta {_fmt(meta_total)}  =  "
            f"{pct_total if pct_total is not None else '-'}%\n"
        ))

        self.stdout.write(f"  {'FACCAO':<26}{'REALIZADO':>12}{'META':>12}{'%':>8}")
        self.stdout.write("  " + "-" * 58)
        for _, r in rank.iterrows():
            pct = f"{r['PCT']:.1f}" if r["PCT"] is not None else "-"
            self.stdout.write(
                f"  {str(r['FACCAO'])[:25]:<26}{_fmt(r['QUANTIDADE']):>12}{_fmt(r['META_MES']):>12}{pct:>8}"
            )
