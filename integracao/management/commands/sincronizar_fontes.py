"""
Roda manualmente a sincronização Sheets -> Postgres de uma ou todas as fontes
registradas em `integracao.sync_registry` (o agendador em background já faz
isso sozinho — este comando é só pra forçar fora do intervalo normal, ex. logo
depois de migrar um domínio novo).

Uso:
    python manage.py sincronizar_fontes --todas
    python manage.py sincronizar_fontes --fonte producao_faccoes
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from integracao import sync_registry
from integracao.db_sync import sync_dataframe


class Command(BaseCommand):
    help = "Sincroniza fontes registradas (Sheets -> Postgres) manualmente."

    def add_arguments(self, parser):
        parser.add_argument("--fonte", type=str, default=None, help="Sincroniza apenas uma fonte pela chave.")
        parser.add_argument("--todas", action="store_true", help="Sincroniza todas as fontes registradas.")

    def handle(self, *args, **opts):
        jobs = sync_registry.todos()
        if not jobs:
            self.stdout.write(self.style.WARNING(
                "Nenhuma fonte registrada em integracao.sync_registry ainda — "
                "os domínios (Produção, Corte, Carteira...) ainda não foram "
                "migrados pro Postgres."
            ))
            return

        if opts["fonte"]:
            job = sync_registry.obter(opts["fonte"])
            if job is None:
                chaves = ", ".join(sorted(j.chave for j in jobs))
                raise CommandError(f"Fonte '{opts['fonte']}' não registrada. Disponíveis: {chaves}")
            alvo = [job]
        elif opts["todas"]:
            alvo = jobs
        else:
            raise CommandError("Use --fonte=<chave> ou --todas.")

        self.stdout.write(self.style.MIGRATE_HEADING(f"Sincronizando {len(alvo)} fonte(s)...\n"))
        ok = 0
        falhas = 0
        for job in alvo:
            df = job.carregar()
            sucesso = sync_dataframe(job.chave, job.tabela, df, label=job.label)
            if sucesso:
                ok += 1
                self.stdout.write(self.style.SUCCESS(f"  [OK] {job.label:<40} {len(df):>5} linhas -> {job.tabela}"))
            else:
                falhas += 1
                self.stdout.write(self.style.ERROR(f"  [X ] {job.label:<40} falhou (ver FonteSincronizada.erro)"))

        resumo = f"\nResultado: {ok} OK, {falhas} falha(s)."
        style = self.style.SUCCESS if falhas == 0 else self.style.WARNING
        self.stdout.write(style(resumo))
