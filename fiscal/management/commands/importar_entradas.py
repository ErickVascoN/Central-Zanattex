"""
Carga em lote de NF-e de entrada — o caminho pro histórico de 8.000+
arquivos. Reusa a mesma `fiscal.importador.confirmar_importacao` que o
upload web usa (única fonte de verdade da regra de identificação/gravação),
só que iterando uma pasta inteira em vez de receber upload. Idempotente:
rodar de novo sobre a mesma pasta não duplica nada (dedupe por
chave_acesso, dentro de `confirmar_importacao`).

Uso:
    python manage.py importar_entradas /caminho/pasta --dry-run
    python manage.py importar_entradas /caminho/pasta --relatorio saida.csv
"""
from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from fiscal import importador
from fiscal.nfe_xml import XmlInvalido

_TAMANHO_LOTE_PROGRESSO = 200


class Command(BaseCommand):
    help = "Importa em lote um diretório de XMLs de NF-e (histórico de entradas)."

    def add_arguments(self, parser):
        parser.add_argument("pasta", type=str, help="Diretório com os arquivos .xml")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Só simula (parse + identificação), não grava nada no banco.")
        parser.add_argument(
            "--relatorio", type=str, default=None,
            help="Caminho de um .csv com uma linha por arquivo pulado/pendente/com erro.")

    def handle(self, *args, **options):
        pasta = Path(options["pasta"])
        if not pasta.is_dir():
            raise CommandError(f'"{pasta}" não é um diretório.')

        arquivos = sorted(pasta.glob("*.xml"))
        if not arquivos:
            self.stdout.write(self.style.WARNING("Nenhum arquivo .xml encontrado."))
            return

        dry_run = options["dry_run"]
        linhas_relatorio: list[dict] = []
        contagem = {"importada": 0, "duplicada": 0, "cliente_pendente": 0, "erro": 0}

        for i, caminho in enumerate(arquivos, start=1):
            conteudo = caminho.read_bytes()
            try:
                if dry_run:
                    previa = importador.montar_previa(conteudo, caminho.name)
                    if previa.ja_importada:
                        status = "duplicada"
                    elif previa.identificacao.cliente is None:
                        status = "cliente_pendente"
                    else:
                        status = "importada"  # seria importada, nada foi gravado
                    cliente_nome = previa.identificacao.nome_cliente
                else:
                    resultado = importador.confirmar_importacao(conteudo, caminho.name)
                    status = resultado.status
                    cliente_nome = resultado.nome_cliente
            except XmlInvalido as e:
                status, cliente_nome = "erro", ""
                linhas_relatorio.append({"arquivo": caminho.name, "status": status, "detalhe": str(e)})
                contagem["erro"] += 1
                continue

            contagem[status] += 1
            if status != "importada":
                linhas_relatorio.append({
                    "arquivo": caminho.name, "status": status,
                    "detalhe": cliente_nome if status == "cliente_pendente" else "",
                })

            if i % _TAMANHO_LOTE_PROGRESSO == 0:
                self.stdout.write(f"{i}/{len(arquivos)} processados...")

        if options["relatorio"] and linhas_relatorio:
            caminho_csv = Path(options["relatorio"])
            with caminho_csv.open("w", newline="", encoding="utf-8") as f:
                escritor = csv.DictWriter(f, fieldnames=["arquivo", "status", "detalhe"])
                escritor.writeheader()
                escritor.writerows(linhas_relatorio)
            self.stdout.write(f"Relatório escrito em {caminho_csv}")

        prefixo = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefixo}Total: {len(arquivos)} — importadas: {contagem['importada']}, "
            f"duplicadas: {contagem['duplicada']}, cliente pendente: {contagem['cliente_pendente']}, "
            f"erro: {contagem['erro']}."
        ))
