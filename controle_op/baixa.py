"""Baixa da OP — o encerramento de ponta a ponta: "esta OP está fechada?
Quando, por quem, com qual Balanço congelado?"

Duas regras, nenhuma negociável:
1. Toda OS precisa ter número do ERP — é o vínculo que o mini-ERP da OP
   inteiro existe pra criar (ver controle_op/models.py::EnvioProducao).
   Baixar sem isso deixaria a OP formalmente encerrada com um vínculo pela
   metade, que ninguém mais vai voltar a completar.
2. Se o Balanço de material não fechou exato (nem FECHADO nem
   NAO_APLICAVEL — DIVERGENTE, INCOMPLETO ou ainda EM_PROCESSO), baixar
   exige dizer o motivo. Nunca trava a baixa por causa disso (V6 do plano:
   corte parcial legítimo não pode travar a OP pra sempre), só exige que a
   divergência fique documentada, não silenciada.

O Balanço no momento da baixa vira um SNAPSHOT congelado (`FechamentoOP.
balanco_snapshot`) — não recalculado ao vivo depois. Um corte lançado por
engano semanas depois de uma OP já baixada não pode fazer o balanço "mudar
de ideia" silenciosamente sobre uma OP que já foi dada como encerrada."""
from __future__ import annotations

from dataclasses import asdict

from django.utils import timezone

from corte.aproveitamento import calcular_aproveitamento

from .balanco import StatusBalanco, calcular_balanco
from .models import FechamentoOP
from .producao import calcular_producao, producao_por_op


class ErroBaixaOP(Exception):
    """Mensagem pronta pra mostrar na tela — sempre explica o motivo, nunca
    só recusa em silêncio."""


# Status que NÃO exigem motivo_divergencia pra baixar: FECHADO é o caso
# ideal (bateu exato); NAO_APLICAVEL é a unidade sem grandeza de material
# (Cortina/Itaju) — cobrar motivo de uma divergência que a fonte real nem
# rastreia seria pedir explicação de algo que não existe.
_STATUS_SEM_MOTIVO_OBRIGATORIO = (StatusBalanco.FECHADO, StatusBalanco.NAO_APLICAVEL)


def _snapshot_balanco(balanco) -> dict:
    return asdict(balanco)


def baixar_op(programacao, usuario, *, motivo_divergencia: str = "") -> FechamentoOP:
    envios = list(programacao.envios_producao.all())
    sem_numero = [e for e in envios if e.sem_numero]
    if sem_numero:
        raise ErroBaixaOP(
            f"{len(sem_numero)} OS ainda sem número do ERP — complete o vínculo "
            "(ou corrija o envio, se foi lançado por engano) antes de baixar a OP.")

    aproveitamento = calcular_aproveitamento(programacao)
    acumulada = producao_por_op(programacao)
    producao = calcular_producao(programacao, produzido_total=acumulada.produzido_total)
    balanco = calcular_balanco(
        programacao, aproveitamento=aproveitamento, producao=producao, acumulada=acumulada)

    motivo_divergencia = (motivo_divergencia or "").strip()
    if balanco.status not in _STATUS_SEM_MOTIVO_OBRIGATORIO and not motivo_divergencia:
        raise ErroBaixaOP(
            f"Balanço {balanco.status_label.lower()} — diga o motivo antes de baixar "
            "a OP (a divergência precisa ficar documentada, nunca em silêncio).")

    fechamento, _ = FechamentoOP.objects.get_or_create(programacao=programacao)
    fechamento.op_baixada = True
    fechamento.op_baixada_em = timezone.now()
    fechamento.op_baixada_por = usuario
    fechamento.balanco_snapshot = _snapshot_balanco(balanco)
    fechamento.balanco_status = balanco.status
    fechamento.motivo_divergencia = motivo_divergencia
    fechamento.save()
    return fechamento


def reabrir_op(programacao, usuario) -> FechamentoOP:
    """Desfaz a baixa — o snapshot FICA (histórico de que já foi baixada
    uma vez, com que balanço), só o status de "baixada" volta atrás."""
    fechamento = getattr(programacao, "fechamento", None)
    if fechamento is None or not fechamento.op_baixada:
        raise ErroBaixaOP("Esta OP não está baixada.")
    fechamento.op_baixada = False
    fechamento.op_baixada_em = None
    fechamento.op_baixada_por = None
    fechamento.save()
    return fechamento


def op_esta_baixada(programacao) -> bool:
    return bool(getattr(getattr(programacao, "fechamento", None), "op_baixada", False))
