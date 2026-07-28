"""
Backup do Postgres via pg_dump — grava um .sql.gz com timestamp.

Uso manual:
    python manage.py backup_postgres --destino ./backups

Em produção (Fly.io), ainda precisa ser agendado externamente (Fly não tem
cron nativo) — ver Fase 4 do plano de migração para as opções (Fly Machine
dedicado com --schedule, ou um workflow do GitHub Actions rodando este
comando via SSH/API). Sem agendamento, esse comando só protege quem lembrar
de rodar manualmente.
"""

from __future__ import annotations

import gzip
import os
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Faz backup do Postgres (pg_dump) e grava um arquivo .sql.gz com timestamp."

    def add_arguments(self, parser):
        parser.add_argument(
            "--destino", type=str, default=str(settings.BASE_DIR / "backups"),
            help="Diretório onde salvar o backup (default: BASE_DIR/backups).",
        )

    def handle(self, *args, **opts):
        db = settings.DATABASES["default"]
        destino = Path(opts["destino"])
        destino.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        arquivo = destino / f"central_zanattex_{timestamp}.sql.gz"

        cmd = [
            "pg_dump",
            "-h", db.get("HOST") or "localhost",
            "-p", str(db.get("PORT") or 5432),
            "-U", db.get("USER") or "postgres",
            "-d", db["NAME"],
            "--no-owner", "--no-privileges",
        ]
        env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}

        try:
            resultado = subprocess.run(cmd, env=env, capture_output=True, check=True)
        except FileNotFoundError:
            raise CommandError(
                "pg_dump não encontrado no PATH — precisa do pacote "
                "postgresql-client (já incluído no Dockerfile de produção)."
            )
        except subprocess.CalledProcessError as e:
            raise CommandError(f"pg_dump falhou: {e.stderr.decode(errors='replace')[:500]}")

        with gzip.open(arquivo, "wb") as f:
            f.write(resultado.stdout)

        tamanho_kb = arquivo.stat().st_size / 1024
        self.stdout.write(self.style.SUCCESS(f"Backup salvo: {arquivo} ({tamanho_kb:.1f} KB)"))
