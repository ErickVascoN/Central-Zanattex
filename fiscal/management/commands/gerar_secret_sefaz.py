"""
Monta/atualiza o secret dos certificados A1 do SEFAZ a partir de um arquivo
.pfx local, sem nunca imprimir o secret (certificado nem senha) na tela ou
salvar o .pfx em lugar nenhum do projeto — só lê os bytes, pede a senha
(oculta, via getpass) e grava o JSON em base64 num arquivo local.

Preserva outros CNPJs já cadastrados (rollout em fases — ver
memory/sefaz-cancelamento-plano.md): rodar de novo com outro --cnpj só
adiciona, não substitui os que já estavam lá.

Por que um arquivo e não direto no `.env`: 2+ certificados juntos nesse JSON
facilmente passam de 32.767 caracteres, limite de variável de ambiente do
Windows — acima disso o `environ.Env.read_env` quebra ao carregar o `.env`
inteiro, derrubando o projeto inteiro em dev local (não existe no Linux do
Fly). Por isso o valor vai pra um arquivo à parte (`fiscal_certificados_local.b64`
na raiz do projeto, fora do git — ver .gitignore) e só o CAMINHO desse
arquivo entra no `.env`, em FISCAL_SEFAZ_CERTIFICADOS_ARQUIVO (curto, não tem
esse problema). Ver settings.py::FISCAL_SEFAZ_CERTIFICADOS_ARQUIVO.

Uso (local, nunca em produção — em produção é `fly secrets set
FISCAL_SEFAZ_CERTIFICADOS_JSON=<valor>` com o mesmo JSON, colado direto no
comando do Fly, não num arquivo; produção roda em Linux e não tem o limite
acima, então lá o secret continua sendo só a variável de ambiente mesmo):
    python manage.py gerar_secret_sefaz --pfx "caminho\\certificado.pfx" --cnpj 14601572000130
"""
from __future__ import annotations

import base64
import getpass
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

_CHAVE_ARQUIVO = "FISCAL_SEFAZ_CERTIFICADOS_ARQUIVO"
_CHAVE_JSON = "FISCAL_SEFAZ_CERTIFICADOS_JSON"
_NOME_ARQUIVO_PADRAO = "fiscal_certificados_local.b64"


class Command(BaseCommand):
    help = ("Gera/atualiza o secret dos certificados SEFAZ a partir de um .pfx local, "
            "gravando num arquivo fora do .env (não imprime o secret).")

    def add_arguments(self, parser):
        parser.add_argument("--pfx", required=True, help="Caminho do arquivo .pfx (certificado A1).")
        parser.add_argument("--cnpj", required=True, help="CNPJ (só dígitos) dono desse certificado.")
        parser.add_argument(
            "--out", default=None,
            help=f"Caminho do arquivo de certificados a atualizar (padrão: {_NOME_ARQUIVO_PADRAO} "
                 "na raiz do projeto).")
        parser.add_argument(
            "--env", default=None,
            help="Caminho do .env a atualizar com a referência ao arquivo (padrão: .env na raiz do projeto).")
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

        caminho_arquivo = (Path(options["out"]) if options["out"]
                            else Path(settings.BASE_DIR) / _NOME_ARQUIVO_PADRAO)

        # Preserva o que já existia (outros CNPJs) — nunca lido a partir do
        # settings já carregado (pode estar vazio em dev), sempre do arquivo
        # bruto, pra não perder entradas que settings.py não decodificou por
        # algum motivo.
        dados_atuais: dict = {}
        if caminho_arquivo.is_file():
            atual = caminho_arquivo.read_text(encoding="ascii").strip()
            if atual:
                try:
                    dados_atuais = json.loads(base64.b64decode(atual))
                except Exception as e:
                    raise CommandError(
                        f'"{caminho_arquivo}" existente não é um JSON base64 válido ({e}) — '
                        "corrija ou apague o arquivo manualmente antes de rodar de novo.") from None

        dados_atuais[cnpj] = {
            "pfx_b64": base64.b64encode(pfx_bytes).decode("ascii"),
            "senha": senha,
        }
        novo_valor = base64.b64encode(json.dumps(dados_atuais).encode("utf-8")).decode("ascii")
        caminho_arquivo.write_text(novo_valor, encoding="ascii")

        # Aponta o .env pro arquivo (referência curta, nunca o secret em si) —
        # também limpa um FISCAL_SEFAZ_CERTIFICADOS_JSON antigo direto no .env,
        # se sobrou de antes desse comando existir, pra não ter duas fontes
        # conflitantes (ver fiscal/sefaz.py::_certificados — arquivo tem prioridade).
        caminho_env = Path(options["env"]) if options["env"] else Path(settings.BASE_DIR) / ".env"
        linhas = caminho_env.read_text(encoding="utf-8").splitlines() if caminho_env.exists() else []
        nova_linha = f"{_CHAVE_ARQUIVO}={caminho_arquivo.name}"
        linhas = [l for l in linhas if not l.startswith(f"{_CHAVE_JSON}=")]
        substituida = False
        for i, linha in enumerate(linhas):
            if linha.startswith(f"{_CHAVE_ARQUIVO}="):
                linhas[i] = nova_linha
                substituida = True
                break
        if not substituida:
            linhas.append(nova_linha)
        caminho_env.write_text("\n".join(linhas) + "\n", encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(
            f"{caminho_arquivo} atualizado — CNPJs cadastrados agora: "
            f"{', '.join(sorted(dados_atuais))}. (Nenhum valor sensível foi impresso.)"
        ))
        self.stdout.write(
            "Pra produção (Fly), gere o mesmo JSON (base64) e rode `fly secrets set "
            f"{_CHAVE_JSON}=<valor>` — a produção continua usando a variável de ambiente direto, "
            "não esse arquivo.")
