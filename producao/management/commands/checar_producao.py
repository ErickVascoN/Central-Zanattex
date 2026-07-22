"""
Diagnóstico da produção de facções (fonte viva da produção diária atual).
Carrega load_faccoes() e imprime um resumo para conferir os números contra o
dashboard atual da Central antes de montar a UI.

Uso:
    python manage.py checar_producao
    python manage.py checar_producao --mes 7 --ano 2026
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from producao.faccao_loader import load_faccoes


def _fmt(v) -> str:
    return f"{int(v):,}".replace(",", ".")


class Command(BaseCommand):
    help = "Carrega a produção de facções (ao vivo do Sheets) e imprime um resumo."

    def add_arguments(self, parser):
        parser.add_argument("--mes", type=int, default=None)
        parser.add_argument("--ano", type=int, default=None)

    def handle(self, *args, **opts):
        df = load_faccoes()
        if df is None or df.empty:
            self.stderr.write(self.style.ERROR("Sem dados de facções."))
            return

        df = df.copy()
        df["Ano"] = df["DATA"].dt.year
        df["Mes"] = df["DATA"].dt.month

        if opts["ano"]:
            df = df[df["Ano"] == opts["ano"]]
        if opts["mes"]:
            df = df[df["Mes"] == opts["mes"]]

        if df.empty:
            self.stdout.write(self.style.WARNING("Nenhuma produção no filtro informado."))
            return

        total = df["QUANTIDADE"].sum()
        d_min = df["DATA"].min().strftime("%d/%m/%Y")
        d_max = df["DATA"].max().strftime("%d/%m/%Y")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nProducao de faccoes | {len(df):,} linhas | {d_min} a {d_max}".replace(",", ".")
        ))
        self.stdout.write(self.style.SUCCESS(f"  TOTAL PRODUZIDO: {_fmt(total)} pcs\n"))

        self.stdout.write("Por faccao:")
        por_fac = df.groupby("FACCAO")["QUANTIDADE"].sum().sort_values(ascending=False)
        for fac, q in por_fac.items():
            self.stdout.write(f"  {fac:<28} {_fmt(q):>10}")

        self.stdout.write("\nPor mes:")
        por_mes = df.groupby(["Ano", "Mes"])["QUANTIDADE"].sum().sort_index()
        for (ano, mes), q in por_mes.items():
            self.stdout.write(f"  {int(ano)}-{int(mes):02d}  {_fmt(q):>10}")

        self.stdout.write("\nTop 5 clientes:")
        por_cli = df.groupby("CLIENTE")["QUANTIDADE"].sum().sort_values(ascending=False).head(5)
        for cli, q in por_cli.items():
            self.stdout.write(f"  {cli:<28} {_fmt(q):>10}")
