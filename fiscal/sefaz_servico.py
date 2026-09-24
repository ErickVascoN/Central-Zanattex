"""
Checagem periódica de cancelamento na SEFAZ — a rede de segurança da Fase 2
(ver memory/sefaz-cancelamento-plano.md). Cobre os dois casos que o
barramento no ato do import (fiscal/importador.py) não resolve sozinho:
(a) SEFAZ fora do ar/consumo indevido no momento do import, (b) nota que já
estava valendo no saldo e só é cancelada DEPOIS de importada.

Sem filtro de janela de dias, de propósito: a remontagem de dados em
produção reimporta histórico inteiro de uma vez, tudo fora de qualquer
janela recente — `verificar_cancelamentos` varre por `situacao_sefaz_verificada_em`
(nulas e mais antigas primeiro) até um teto por rodada
(`FISCAL_SEFAZ_LOTE_CRON`), então o backlog inicial vai sendo consumido ao
longo de várias rodadas do cron sem precisar de comando/script à parte.

Diferente do caso "nasce cancelada" do import (resolvido sozinho, nunca
teve saldo de verdade): uma nota que ESTA função encontra cancelada já
estava contando no saldo — não é excluída automaticamente, vira
PendenciaMatching(motivo=CANCELADA_SEFAZ) pra alguém decidir na tela
Pendências (fiscal/views.py::resolver_pendencia)."""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from . import sefaz
from .models import NotaFiscal, PendenciaMatching


@dataclass
class ResumoVerificacao:
    verificadas: int = 0
    novas_para_revisao: int = 0
    erros: int = 0


def _notas_a_verificar(chaves: list[str] | None, limite: int):
    qs = NotaFiscal.objects.filter(situacao=NotaFiscal.Situacao.VALIDA)
    if chaves is not None:
        return list(qs.filter(chave_acesso__in=chaves))
    # Nulas primeiro (nunca verificadas), depois as mais antigas — é o que
    # faz o backlog inicial (go-live) ser consumido em várias rodadas sem
    # nenhuma nota ficar pra trás pra sempre.
    return list(qs.order_by(F("situacao_sefaz_verificada_em").asc(nulls_first=True))[:limite])


def verificar_cancelamentos(chaves: list[str] | None = None, limite: int | None = None) -> ResumoVerificacao:
    """`chaves`: subconjunto específico (botão manual numa nota) — ignora o
    teto e a ordenação por mais antiga, verifica só essas. `limite`: default
    `settings.FISCAL_SEFAZ_LOTE_CRON`, só vale quando `chaves` é None."""
    limite = limite if limite is not None else settings.FISCAL_SEFAZ_LOTE_CRON
    notas = _notas_a_verificar(chaves, limite)
    resumo = ResumoVerificacao()
    if not notas:
        return resumo

    por_chave = {n.chave_acesso: n for n in notas}
    resultados = sefaz.consultar_situacao_lote([(n.chave_acesso, n.centro_custo) for n in notas])

    agora = timezone.now()
    for chave, resultado in resultados.items():
        nota = por_chave[chave]
        if resultado.situacao == sefaz.Situacao.NAO_VERIFICADA:
            # Erro/timeout/consumo indevido — não atualiza
            # situacao_sefaz_verificada_em, ela continua "a mais antiga" e
            # entra na próxima rodada de novo.
            resumo.erros += 1
            continue

        resumo.verificadas += 1
        if not resultado.cancelada:
            NotaFiscal.objects.filter(pk=nota.pk).update(situacao_sefaz_verificada_em=agora)
            continue

        # Cancelada DEPOIS de já valer no saldo — não sai sozinha, vira
        # pendência (ver docstring do módulo).
        NotaFiscal.objects.filter(pk=nota.pk).update(
            situacao_sefaz_verificada_em=agora,
            cancelamento_origem=NotaFiscal.CancelamentoOrigem.POS_IMPORTACAO,
            cancelamento_detectado_em=agora,
            protocolo_cancelamento=resultado.protocolo,
            motivo_cancelamento=resultado.xmotivo,
            cancelamento_revisao_pendente=True,
        )
        if not PendenciaMatching.objects.filter(
                motivo=PendenciaMatching.Motivo.CANCELADA_SEFAZ, nota_fiscal=nota, resolvido=False).exists():
            PendenciaMatching.objects.create(
                motivo=PendenciaMatching.Motivo.CANCELADA_SEFAZ, nota_fiscal=nota,
                detalhe=(
                    f"SEFAZ reporta a NF {nota.n_nf} ({nota.get_tipo_display()}) como cancelada "
                    f"(protocolo {resultado.protocolo or '—'}, {resultado.xmotivo or 'motivo não informado'}) "
                    "— ela já estava contando no saldo. Confira e decida: excluir do saldo ou manter mesmo assim."))
            resumo.novas_para_revisao += 1

    return resumo
