"""Backfill único de cutover: importa as OPs de Programação de Corte que
ainda estão em andamento (não-Concluídas) da planilha atual pra
ProgramacaoCorte, pra Gestão de Corte não nascer vazia. OPs já concluídas
ficam só na planilha legada — não é migração histórica completa, é só o que
está ativo no momento em que o comando roda.

Mapeamento aproximado (a planilha não tem os mesmos campos 1:1):
- LOCAL da planilha (texto livre, ex.: "GIATTEX-ZANATTA", "BARRACAO CORTE")
  é resolvido pra um dos 3 Local via palavra-chave (mesma lógica de
  programacao/servicos.py::_LOCAL_FONTE_KW) — ambíguos/desconhecidos caem
  em ZANATTEX por padrão e são contados à parte no resumo final.
- destino_costura não existe na planilha legada — fica "A definir (import)".
- categoria/tamanho não existem separados — ficam em branco.
- saldo_carteira_snap usa a própria QNT. PROG (não temos o saldo real da
  Carteira no momento em que a OP foi programada originalmente).

Idempotente: roda com `update_or_create` numa chave (pedido, semana, local,
produto) — rodar de novo não duplica, só atualiza."""
from __future__ import annotations

from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from corte.models import ProgramacaoCorte
from programacao import servicos

_LOCAL_FONTE_KW = {
    ProgramacaoCorte.Local.GIATTEX: ["GIATTEX", "GGTEX", "GIATTA", "IACANGA"],
    ProgramacaoCorte.Local.ZANATTEX: ["ZANATTEX", "AREALVA", "ZANATTA"],
    ProgramacaoCorte.Local.LENCOL: ["LENCOL", "LENÇOL"],
}

_STATUS_MAP = {
    "Pendente": ProgramacaoCorte.Status.PENDENTE,
    "Parcial": ProgramacaoCorte.Status.PARCIAL,
    "Concluído": ProgramacaoCorte.Status.CONCLUIDO,
}


def _resolver_local(texto: str) -> tuple[str, bool]:
    """Retorna (Local, era_ambiguo_ou_desconhecido)."""
    up = (texto or "").upper()
    for local, palavras in _LOCAL_FONTE_KW.items():
        if any(p in up for p in palavras):
            return local, False
    return ProgramacaoCorte.Local.ZANATTEX, True


def _parse_data(texto) -> object:
    if texto is None or (isinstance(texto, float) and texto != texto):  # NaN
        return None
    texto = str(texto).strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date()
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Importa da planilha as OPs de Programação de Corte em andamento pra ProgramacaoCorte (cutover único, idempotente)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--usuario", default=None,
            help="Username a atribuir como criado_por (padrão: primeiro superuser encontrado).")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Só mostra o que seria importado, sem gravar no banco.")

    def handle(self, *args, **options):
        User = get_user_model()
        if options["usuario"]:
            try:
                usuario = User.objects.get(username=options["usuario"])
            except User.DoesNotExist:
                raise CommandError(f"Usuário {options['usuario']!r} não encontrado.")
        else:
            usuario = User.objects.filter(is_superuser=True).order_by("id").first()
            if not usuario:
                raise CommandError("Nenhum superuser encontrado — informe --usuario.")

        # Lê da PLANILHA, não de `carregar_programacao()` — essa passou a ler o
        # próprio ProgramacaoCorte, então o backfill viraria um no-op num banco
        # vazio, que é exatamente o caso em que ele precisa rodar (produção
        # nasceu sem o cutover — reportado 2026-09-10).
        df_prog = servicos.carregar_programacao_do_sheets()
        if df_prog.empty:
            self.stdout.write(self.style.WARNING("Planilha de Programação vazia — nada a importar."))
            return

        df_cortes = servicos.carregar_cortes()
        df_enriched = servicos.enriquecer(df_prog, df_cortes)
        em_andamento = df_enriched[df_enriched["STATUS_CORTE_OP"] != "Concluído"]

        criados = atualizados = ambiguos = 0
        dry_run = options["dry_run"]

        for _, row in em_andamento.iterrows():
            pedido = str(row.get("PED. CLIENTE") or "").strip()
            if not pedido:
                continue
            produto = str(row.get("DESCRIÇÃO DO PRODUTO") or row.get("PRODUTO") or "").strip()
            local, ambiguo = _resolver_local(str(row.get("LOCAL", "")))
            if ambiguo:
                ambiguos += 1
            status = _STATUS_MAP.get(str(row.get("STATUS_CORTE_OP", "")).strip(), ProgramacaoCorte.Status.PENDENTE)
            qnt = int(row.get("QNT. PROG") or 0)
            if qnt <= 0:
                continue

            defaults = dict(
                origem=ProgramacaoCorte.Origem.IMPORTACAO,
                op_interna=str(row.get("OP INTERNA") or row.get("PED. INT") or "").strip(),
                oc=str(row.get("OC") or "").strip(),
                cliente=str(row.get("CLIENTE") or "").strip(),
                categoria="",
                tamanho="",
                saldo_carteira_snap=qnt,
                qnt_programada=qnt,
                local=local,
                destino_costura="A definir (import)",
                data_inicio=_parse_data(row.get("DATA INICIO")),
                prev_industrializacao=_parse_data(row.get("PREV. INDUSTRIALIZAÇÃO")),
                data_finalizado=_parse_data(row.get("DATA FINALIZADO")),
                status=status,
                criado_por=usuario,
            )

            if dry_run:
                criados += 1
                continue

            _, criado = ProgramacaoCorte.objects.update_or_create(
                pedido=pedido, semana=str(row.get("SEMANA", "")).strip(),
                local=local, produto=produto,
                defaults=defaults,
            )
            if criado:
                criados += 1
            else:
                atualizados += 1

        acao = "seriam importadas" if dry_run else "importadas"
        self.stdout.write(self.style.SUCCESS(
            f"{criados} OPs {acao}, {atualizados} atualizadas. "
            f"{ambiguos} com local ambíguo/desconhecido (caíram em ZANATTEX por padrão — revisar)."
        ))
