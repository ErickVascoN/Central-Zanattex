"""Rollup do pós-corte de uma OP: envio/retorno lançados manualmente
(controle_op/models.py) + produção diária puxada AO VIVO da planilha de
facções (mesma fonte que já alimenta a Análise de Produção — ver
producao/faccao_loader.py) — não pedimos de novo pro usuário uma informação
que o sistema já recebe automatizada todo dia.

O casamento "essa linha da planilha de facção é dessa OP" é por
cliente+produto+facção (não existe coluna de OP/pedido na planilha de
facções hoje) — por isso é rotulado como automático/referência, não como
verdade absoluta: se dois pedidos do mesmo cliente com o mesmo produto
estiverem rodando ao mesmo tempo na mesma facção, a soma pode incluir peças
de ambos. Sempre alinhado com o registro manual de retorno pra fechar de
fato o número da OP."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from corte.models import ProgramacaoCorte
from integracao.normalize import normalize_text

# Mesmo limiar usado em corte/aproveitamento.py::LIMIAR_CONCLUIDO — Produção
# fecha com a mesma régua de "bateu com o programado" que o Corte já usa.
LIMIAR_CONCLUIDO = 0.96


class StatusProducao:
    NAO_INICIADO = "NAO_INICIADO"
    EM_INDUSTRIALIZACAO = "EM_INDUSTRIALIZACAO"
    CONCLUIDO = "CONCLUIDO"

    LABELS = {
        NAO_INICIADO: "Não iniciado",
        EM_INDUSTRIALIZACAO: "Em industrialização",
        CONCLUIDO: "Concluído",
    }


@dataclass
class ProducaoOP:
    enviado_pecas: int = 0
    # OS lançadas sem o número do ERP — o vínculo com o ERP fica pela
    # metade enquanto houver alguma, então é pendência da OP (a Baixa da OP
    # vai barrar por isso), não só um campo em branco na tabela.
    envios_sem_numero: int = 0
    retornado_pecas: int = 0
    retalho_producao_kg: float | None = None
    saldo_industria: int = 0
    status: str = StatusProducao.NAO_INICIADO
    producao_auto_total: int = 0
    producao_auto_linhas: list = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return StatusProducao.LABELS[self.status]


def calcular_producao(programacao: ProgramacaoCorte) -> ProducaoOP:
    envios = list(programacao.envios_producao.all())
    retornos = list(programacao.retornos_producao.all())

    enviado = sum(e.quantidade_pecas for e in envios)
    retornado = sum(r.quantidade_pecas for r in retornos)
    retalho_vals = [float(r.retalho_kg) for r in retornos if r.retalho_kg is not None]

    if enviado <= 0:
        status = StatusProducao.NAO_INICIADO
    else:
        alvo = programacao.qnt_programada or enviado
        status = (
            StatusProducao.CONCLUIDO if retornado / alvo >= LIMIAR_CONCLUIDO
            else StatusProducao.EM_INDUSTRIALIZACAO
        )

    resultado = ProducaoOP(
        enviado_pecas=enviado,
        envios_sem_numero=sum(1 for e in envios if e.sem_numero),
        retornado_pecas=retornado,
        retalho_producao_kg=sum(retalho_vals) if retalho_vals else None,
        saldo_industria=max(enviado - retornado, 0),
        status=status,
    )
    return resultado


def producao_diaria_auto(programacao: ProgramacaoCorte) -> tuple[list[dict], int]:
    """Linhas da planilha de facções que casam com cliente+produto (e
    facção, quando o destino_costura bate) desta OP, a partir da data de
    início do corte. Retorna ([], 0) silenciosamente se a planilha estiver
    indisponível — é um painel de referência, não pode derrubar a tela de
    Controle de OP se o Sheets falhar."""
    try:
        from producao.faccao_loader import load_faccoes
        df = load_faccoes()
    except Exception:
        return [], 0

    if df is None or df.empty:
        return [], 0

    cliente_alvo = normalize_text(programacao.cliente)
    produto_alvo = normalize_text(programacao.produto)
    destino_alvo = normalize_text(programacao.destino_costura)
    data_corte = programacao.data_inicio or programacao.criado_em.date()

    linhas = []
    for _, row in df.iterrows():
        if normalize_text(row.get("CLIENTE")) != cliente_alvo:
            continue
        if normalize_text(row.get("PRODUTO")) != produto_alvo:
            continue
        data_linha = row.get("DATA")
        if hasattr(data_linha, "date"):
            data_linha = data_linha.date()
        if isinstance(data_linha, date) and data_linha < data_corte:
            continue
        linhas.append({
            "data": data_linha,
            "faccao": row.get("FACCAO"),
            "prestador": row.get("PRESTADOR"),
            "quantidade": row.get("QUANTIDADE"),
            "match_faccao": normalize_text(row.get("FACCAO")) == destino_alvo,
        })

    linhas.sort(key=lambda item: item["data"] or date.min, reverse=True)
    total = sum(int(item["quantidade"] or 0) for item in linhas)
    return linhas, total
