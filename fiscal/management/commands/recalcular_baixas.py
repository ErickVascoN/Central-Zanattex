"""
Refaz todo o casamento automático do Saldo Fiscal (ver
fiscal/matching.py::recalcular_baixas). Sem --aplicar, só simula: roda tudo
numa transação e desfaz no fim, mostrando o que mudaria.

    python manage.py recalcular_baixas            # simulação
    python manage.py recalcular_baixas --aplicar  # grava
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from fiscal import matching


class Command(BaseCommand):
    help = "Refaz as baixas automáticas de todas as NFs de saída (preserva resoluções manuais)."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Grava o resultado (sem isso, só simula).")

    def handle(self, *args, aplicar: bool, **options):
        with transaction.atomic():
            r = matching.recalcular_baixas()
            if not aplicar:
                transaction.set_rollback(True)
        self.stdout.write("\n".join([
            f"Notas de saída reprocessadas: {r.notas_reprocessadas}",
            f"Baixas automáticas: {r.vinculos_apagados} apagadas -> {r.vinculos_criados} refeitas",
            f"Pendências abertas: {r.pendencias_apagadas} apagadas -> {r.pendencias_criadas} recriadas",
            f"Notas anuladas por estorno: {r.notas_anuladas}",
            f"Possíveis duplicidades (itens): {r.possiveis_duplicidades}",
        ]))
        self.stdout.write(self.style.SUCCESS("Gravado.") if aplicar
                          else self.style.WARNING("Simulação — nada foi gravado. Use --aplicar pra gravar."))
