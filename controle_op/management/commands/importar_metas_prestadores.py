"""Cadastra Prestador + MetaPrestador a partir da aba "METAS" da planilha de
facções — a mesma fonte que o usuário já mantém pra produção diária,
sincronizada em `producao_metas` (ver `producao/metas.py::load_metas()`).
Primeiro passo de "começar a eliminar a dependência das planilhas, em
pequenos pontos" (ver MetaPrestador em controle_op/models.py): traz o que já
existe pro banco, editável dali em diante direto no admin.

Idempotente: `Prestador.objects.get_or_create` por nome, `MetaPrestador.
objects.update_or_create` por (prestador, produto, cliente) — rodar de novo
não duplica, só atualiza a meta se ela mudou na planilha.

Não traz telefone — essa informação não existe em nenhuma planilha
sincronizada hoje, precisa ser preenchida à mão no admin depois."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from controle_op.models import MetaPrestador, Prestador, opcoes_prestador
from producao.metas import load_metas


class Command(BaseCommand):
    help = "Cadastra Prestador + MetaPrestador a partir da planilha de metas já sincronizada."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Só mostra o que seria feito, não grava nada.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        metas = load_metas()
        if not metas:
            self.stdout.write(self.style.WARNING(
                "producao_metas está vazia — rode o sync (producao/sync.py) antes."))
            return

        canonicos = set(opcoes_prestador())
        fora_da_lista = sorted({d["faccao"] for d in metas} - canonicos)
        if fora_da_lista:
            self.stdout.write(self.style.WARNING(
                "Fora da lista canônica de facções (Prestador.clean() recusaria "
                f"criar): {', '.join(fora_da_lista)} — ignorados."))

        prestadores_criados = 0
        metas_criadas = 0
        metas_atualizadas = 0
        prestadores_cache: dict[str, Prestador] = {}

        for linha in metas:
            nome = linha["faccao"]
            if nome in fora_da_lista:
                continue

            if dry_run:
                ja_existe = Prestador.objects.filter(nome=nome).exists()
                if not ja_existe and nome not in prestadores_cache:
                    prestadores_criados += 1
                    prestadores_cache[nome] = None  # só pra não contar 2x no dry-run
                continue

            prestador = prestadores_cache.get(nome)
            if prestador is None:
                prestador, criado = Prestador.objects.get_or_create(nome=nome)
                prestadores_cache[nome] = prestador
                if criado:
                    prestadores_criados += 1

            _, criada = MetaPrestador.objects.update_or_create(
                prestador=prestador, produto=linha["produto"], cliente=linha["cliente"],
                defaults={"meta_pecas": linha["meta_dia"]},
            )
            if criada:
                metas_criadas += 1
            else:
                metas_atualizadas += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"[dry-run] {prestadores_criados} prestadores novos seriam criados, "
                f"{len(metas)} linhas de meta seriam processadas."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"{prestadores_criados} prestadores novos, {metas_criadas} metas novas, "
            f"{metas_atualizadas} metas atualizadas."))
