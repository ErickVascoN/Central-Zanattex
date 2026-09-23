"""
Testa fiscal/sefaz.py isoladamente — sem depender da importação nem de
nenhuma nota gravada no banco. Pensado pra validar contra o SEFAZ de
homologação antes de plugar a consulta no fluxo de import (ver o plano em
memory/sefaz-cancelamento-plano.md, Fase 1, passo 3): dá pra apontar uma
chave de acesso conhecida como cancelada (caminho feliz) e uma autorizada, e
ver a resposta interpretada (ou o erro cru, se `_interpretar_resposta` ainda
não estiver acertado — ver docstring de fiscal/sefaz.py).

Uso:
    python manage.py verificar_cancelamentos_sefaz --chave 4425... --cnpj 14601572000130
    python manage.py verificar_cancelamentos_sefaz --chave 4425... --chave 4425... --cnpj 14601572000130
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fiscal import sefaz


class Command(BaseCommand):
    help = "Consulta uma ou mais chaves de acesso direto na SEFAZ (fiscal/sefaz.py), sem tocar no banco."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chave", action="append", required=True, dest="chaves",
            help="Chave de acesso (44 dígitos). Pode repetir a flag pra consultar várias de uma vez.")
        parser.add_argument("--cnpj", required=True, help="CNPJ da Zanattex dono da nota (só dígitos).")

    def handle(self, *args, **options):
        chaves: list[str] = options["chaves"]
        cnpj: str = options["cnpj"]

        for chave in chaves:
            if len(chave) != 44 or not chave.isdigit():
                raise CommandError(f'Chave de acesso inválida: "{chave}" (precisa ter 44 dígitos).')

        self.stdout.write(
            f"Ambiente: {settings.FISCAL_SEFAZ_AMBIENTE} — "
            f"UF do CNPJ {cnpj}: {settings.FISCAL_SEFAZ_UF_POR_CNPJ.get(cnpj, '(não cadastrada)')}")

        if len(chaves) == 1:
            resultado = sefaz.consultar_situacao(chaves[0], cnpj)
            self._imprimir(chaves[0], resultado)
            return

        resultados = sefaz.consultar_situacao_lote([(chave, cnpj) for chave in chaves])
        for chave in chaves:
            self._imprimir(chave, resultados[chave])

    def _imprimir(self, chave: str, resultado: sefaz.ResultadoConsultaSefaz) -> None:
        cor = {
            sefaz.Situacao.AUTORIZADA: self.style.SUCCESS,
            sefaz.Situacao.CANCELADA: self.style.WARNING,
            sefaz.Situacao.NAO_VERIFICADA: self.style.ERROR,
        }[resultado.situacao]
        self.stdout.write(cor(f"{chave}: {resultado.situacao}"))
        self.stdout.write(
            f"  cStat={resultado.cstat!r} xMotivo={resultado.xmotivo!r} "
            f"protocolo={resultado.protocolo!r} dhRecbto={resultado.dh_recbto}")
        if resultado.erro:
            self.stdout.write(f"  erro: {resultado.erro}")
