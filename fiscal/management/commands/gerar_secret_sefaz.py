"""
Monta/atualiza o valor de FISCAL_SEFAZ_CERTIFICADOS_JSON a partir de um
arquivo .pfx local, sem nunca imprimir o secret (certificado nem senha) na
tela ou salvar o .pfx em lugar nenhum do projeto — só lê os bytes, pede a
senha (oculta, via getpass) e grava o JSON em base64 direto no `.env`.

Preserva outros CNPJs já cadastrados no `.env` (rollout em fases — ver
memory/sefaz-cancelamento-plano.md): rodar de novo com outro --cnpj só
adiciona, não substitui os que já estavam lá.

Uso (local, nunca em produção — em produção é `fly secrets set` com o mesmo
JSON, gerado do mesmo jeito mas colado direto no comando do Fly, não no
.env):
    python manage.py gerar_secret_sefaz --pfx "caminho\\certificado.pfx" --cnpj 14601572000130
"""
from __future__ import annotations

import base64
import getpass
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

_CHAVE = "FISCAL_SEFAZ_CERTIFICADOS_JSON"


class Command(BaseCommand):
    help = "Gera/atualiza FISCAL_SEFAZ_CERTIFICADOS_JSON no .env a partir de um .pfx local (não imprime o secret)."

    def add_arguments(self, parser):
        parser.add_argument("--pfx", required=True, help="Caminho do arquivo .pfx (certificado A1).")
        parser.add_argument("--cnpj", required=True, help="CNPJ (só dígitos) dono desse certificado.")
        parser.add_argument(
            "--out", default=None,
            help="Caminho do .env a atualizar (padrão: .env na raiz do projeto).")
        parser.add_argument(
            "--senha", default=None,
            help="Só use se a digitação oculta (getpass) não funcionar no seu terminal — "
                 "com essa flag a senha fica visível na tela e no histórico do terminal.")

    def handle(self, *args, **options):
        cnpj = options["cnpj"]
        if not cnpj.isdigit() or len(cnpj) != 14:
            raise CommandError(f'CNPJ inválido: "{cnpj}" (precisa ser só dígitos, 14 caracteres).')

        pfx_path = Path(options["pfx"])
        if not pfx_path.is_file():
            raise CommandError(f'Arquivo não encontrado: "{pfx_path}".')
        pfx_bytes = pfx_path.read_bytes()

        if options["senha"]:
            senha = options["senha"]
        else:
            senha = ""
            for _ in range(3):
                # getpass não mostra nada na tela enquanto você digita — isso
                # é esperado, não é travamento. Se seu terminal não suportar
                # entrada oculta (alguns terminais integrados de IDE têm
                # problema com isso), use --senha em vez de digitar aqui.
                senha = getpass.getpass(
                    f"Senha do certificado ({pfx_path.name}) — nada aparece na tela ao digitar, "
                    "isso é normal, digite e aperte Enter: ")
                if senha:
                    break
                self.stdout.write(self.style.WARNING(
                    "Veio vazio. Se seu terminal não mostra nem esconde nada ao digitar, tente de "
                    "novo com --senha \"sua senha aqui\" em vez de digitar na hora."))
            if not senha:
                raise CommandError(
                    "Senha vazia depois de 3 tentativas — rode de novo com --senha \"sua senha\".")

        # Preserva o que já existia (outros CNPJs) — nunca lido a partir do
        # settings já carregado (pode estar vazio em dev), sempre do .env
        # bruto, pra não perder entradas que settings.py não decodificou por
        # algum motivo.
        dados_atuais: dict = {}
        atual = settings.FISCAL_SEFAZ_CERTIFICADOS_JSON
        if atual:
            try:
                dados_atuais = json.loads(base64.b64decode(atual))
            except Exception as e:
                raise CommandError(
                    f"FISCAL_SEFAZ_CERTIFICADOS_JSON existente no .env não é um JSON base64 válido "
                    f"({e}) — corrija ou remova a linha manualmente antes de rodar de novo.") from None

        dados_atuais[cnpj] = {
            "pfx_b64": base64.b64encode(pfx_bytes).decode("ascii"),
            "senha": senha,
        }
        novo_valor = base64.b64encode(json.dumps(dados_atuais).encode("utf-8")).decode("ascii")

        caminho_env = Path(options["out"]) if options["out"] else Path(settings.BASE_DIR) / ".env"
        linhas = caminho_env.read_text(encoding="utf-8").splitlines() if caminho_env.exists() else []
        nova_linha = f"{_CHAVE}={novo_valor}"
        substituida = False
        for i, linha in enumerate(linhas):
            if linha.startswith(f"{_CHAVE}="):
                linhas[i] = nova_linha
                substituida = True
                break
        if not substituida:
            linhas.append(nova_linha)
        caminho_env.write_text("\n".join(linhas) + "\n", encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(
            f"{_CHAVE} atualizado em {caminho_env} — CNPJs cadastrados agora: "
            f"{', '.join(sorted(dados_atuais))}. (Nenhum valor sensível foi impresso.)"
        ))
        self.stdout.write(
            "Pra produção (Fly), gere o mesmo JSON e rode `fly secrets set "
            f"{_CHAVE}=<valor>` — não deixe esse secret só no .env local.")
