"""Diagnóstico das 4 unidades internas (colaboradores). Confere linhas,
período, nº de colaboradores e total antes de montar a UI.

Uso: python manage.py checar_interno
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from producao.interno_loader import load_interno_unidade
from integracao.fontes import PRODUCAO_INTERNO


def _fmt(v):
    return f"{int(v):,}".replace(",", ".")


class Command(BaseCommand):
    help = "Carrega as unidades internas e imprime um resumo."

    def handle(self, *args, **opts):
        for chave, cfg in PRODUCAO_INTERNO.items():
            df = load_interno_unidade(chave)
            if df is None or df.empty:
                self.stdout.write(self.style.ERROR(f"  [X ] {cfg['label']:<20} sem dados"))
                continue
            d_min = df["DATA"].min().strftime("%d/%m/%Y")
            d_max = df["DATA"].max().strftime("%d/%m/%Y")
            dims = [d for d in ("SETOR", "FUNCAO", "TAMANHO", "CLIENTE")
                    if d in df.columns and df[d].astype(str).str.strip().ne("").any()]
            self.stdout.write(self.style.SUCCESS(
                f"  [OK] {cfg['label']:<20} {len(df):>5} linhas | "
                f"{df['COLABORADOR'].nunique():>3} colab | {_fmt(df['QUANTIDADE'].sum()):>10} pcs | "
                f"{d_min}-{d_max} | dims: {', '.join(dims) or '-'}"
            ))
