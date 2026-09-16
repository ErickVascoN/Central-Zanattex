"""Backfill único: preenche `data_entrada_carteira` nas OPs criadas ANTES
desse campo existir (commit que renomeou prev_industrializacao → prev_corte
e introduziu o snapshot, 2026-09-14). O valor só é capturado no momento da
criação (tela de Nova Programação, lendo a Carteira ao vivo) — não tem como
recuperar de dentro do próprio banco, só casando de novo com a Carteira de
Pedidos sincronizada hoje, pelo número do pedido.

Importante: a Carteira sincronizada só traz pedidos AINDA EM ABERTO (mesma
regra de "saldo em aberto" de carteira/servicos.py) — pedido cujo saldo já
zerou pode ter saído dela de vez, e nesse caso não tem como recuperar a
data (fica None pra sempre, mostrado honestamente como "—", nunca
inventado). Confirmado rodando um dry-run antes: a maioria dos pedidos
antigos ainda aparece na Carteira aberta hoje, só um caso pontual não foi
encontrado.

Idempotente: só toca OP com `data_entrada_carteira` NULA."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from carteira.servicos import carregar_carteira
from corte.models import ProgramacaoCorte


class Command(BaseCommand):
    help = ("Preenche data_entrada_carteira nas OPs criadas antes desse campo existir, "
            "casando pela Carteira de Pedidos sincronizada.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Só mostra o que seria feito, não grava nada.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        df = carregar_carteira()
        if df.empty:
            self.stdout.write(self.style.WARNING(
                "Carteira sincronizada está vazia — rode o sync (carteira/sync.py) antes."))
            return

        # Mesma régua da Nova Programação (programacao/views.py::
        # api_carteira_aberta): quando um pedido tem mais de uma linha na
        # Carteira, a data de entrada é a mais RECENTE delas.
        datas_por_pedido = df.groupby("PEDIDO")["DATA"].max().to_dict()

        qs = ProgramacaoCorte.objects.filter(
            origem=ProgramacaoCorte.Origem.SISTEMA, data_entrada_carteira__isnull=True)

        atualizados = 0
        nao_encontrados = []
        for programacao in qs:
            data = datas_por_pedido.get(programacao.pedido)
            if data is None:
                nao_encontrados.append(programacao.pedido)
                continue
            atualizados += 1
            if not dry_run:
                programacao.data_entrada_carteira = data.date()
                programacao.save(update_fields=["data_entrada_carteira"])

        if nao_encontrados:
            self.stdout.write(self.style.WARNING(
                f"{len(nao_encontrados)} pedido(s) não encontrados na Carteira aberta hoje "
                f"(saldo já deve ter zerado) — ficam sem essa data: "
                f"{', '.join(nao_encontrados)}"))
        prefixo = "[dry-run] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefixo}{atualizados} OP(s) atualizada(s)."))
