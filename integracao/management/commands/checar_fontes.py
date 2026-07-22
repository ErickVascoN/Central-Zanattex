"""
Comando de diagnóstico: baixa cada fonte (Google Sheets) e reporta linhas,
colunas e frescura do cache. Prova que a leitura ao vivo das planilhas está
funcionando no novo app.

Uso:
    python manage.py checar_fontes            # testa as fontes principais
    python manage.py checar_fontes --todas    # inclui produção interna e facções
    python manage.py checar_fontes --fonte corte_arealva
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from integracao import sheets_client
from integracao.fontes import FONTES, todas_as_fontes


class Command(BaseCommand):
    help = "Baixa cada fonte de dados (Google Sheets) e reporta status da leitura ao vivo."

    def add_arguments(self, parser):
        parser.add_argument("--todas", action="store_true", help="Inclui produção interna e facções.")
        parser.add_argument("--fonte", type=str, default=None, help="Testa apenas uma fonte pela chave.")

    def handle(self, *args, **opts):
        fontes = todas_as_fontes() if opts["todas"] else dict(FONTES)
        if opts["fonte"]:
            todas = todas_as_fontes()
            if opts["fonte"] not in todas:
                self.stderr.write(self.style.ERROR(f"Fonte '{opts['fonte']}' não encontrada."))
                return
            fontes = {opts["fonte"]: todas[opts["fonte"]]}

        self.stdout.write(self.style.MIGRATE_HEADING(f"Checando {len(fontes)} fonte(s)...\n"))

        ok = 0
        falhas = 0
        for chave, f in fontes.items():
            label = f.get("label", chave)
            df = sheets_client.get_dataframe(f["id"], f.get("gid", "0"), ttl=0)  # ttl=0 força leitura fresca
            if df is None or df.empty:
                falhas += 1
                self.stdout.write(self.style.ERROR(f"  [X ] {label:<40} sem dados (verifique compartilhamento publico)"))
            else:
                ok += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  [OK] {label:<40} {len(df):>5} linhas | {len(df.columns)} colunas"
                ))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Cache atual:"))
        for c in sheets_client.cache_status():
            self.stdout.write(f"    {c['arquivo']:<50} {c['tamanho_kb']:>7} KB | {c['idade_min']} min")

        resumo = f"\nResultado: {ok} OK, {falhas} falha(s)."
        style = self.style.SUCCESS if falhas == 0 else self.style.WARNING
        self.stdout.write(style(resumo))
