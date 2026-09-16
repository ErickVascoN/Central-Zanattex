"""Rollup do pós-corte de uma OP: envio, apontamento de produção e retorno
lançados manualmente (controle_op/models.py) + a produção diária puxada AO
VIVO da planilha de facções (mesma fonte que já alimenta a Análise de
Produção — ver producao/faccao_loader.py), que fica ao lado como referência.

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
    # O que falta VOLTAR fisicamente, contra o que a facção apontou ter
    # produzido (não contra o enviado nem o programado) — é a mesma régua
    # que decide `status`, ver `calcular_producao`.
    saldo_a_retornar: int = 0
    status: str = StatusProducao.NAO_INICIADO
    producao_auto_total: int = 0
    producao_auto_linhas: list = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return StatusProducao.LABELS[self.status]


def calcular_producao(programacao: ProgramacaoCorte, *,
                      produzido_total: int | None = None) -> ProducaoOP:
    """`produzido_total` vem do apontamento da Fase 2 (`producao_por_op`) —
    é contra ELE que o Retorno reconcilia, não contra `qnt_programada`.

    Esse é o fix do bug que travava OP com corte parcial legítimo pra
    sempre: pedido de 1000, corte parcial de 600 (já cobrado como pendência
    lá no Corte), envio de 600, produção de 600, retorno de 600 — antes
    `alvo` virava `qnt_programada` (1000), então 600/1000 = 60% nunca batia
    o LIMIAR_CONCLUIDO e a OP não fechava. Cobrar o déficit contra o
    programado de novo aqui é cobrar a mesma coisa duas vezes."""
    envios = list(programacao.envios_producao.all())
    retornos = list(programacao.retornos_producao.all())

    enviado = sum(e.quantidade_pecas for e in envios)
    retornado = sum(r.quantidade_pecas for r in retornos)
    retalho_vals = [float(r.retalho_kg) for r in retornos if r.retalho_kg is not None]

    # Sem nenhum apontamento de produção ainda, cai pro enviado como alvo
    # provisório — só pra não travar em NAO_INICIADO por causa de uma
    # etapa anterior (Produção) que ainda não foi preenchida.
    alvo = produzido_total or enviado or 0

    if enviado <= 0:
        status = StatusProducao.NAO_INICIADO
    elif alvo <= 0:
        status = StatusProducao.EM_INDUSTRIALIZACAO
    else:
        status = (
            StatusProducao.CONCLUIDO if retornado / alvo >= LIMIAR_CONCLUIDO
            else StatusProducao.EM_INDUSTRIALIZACAO
        )

    resultado = ProducaoOP(
        enviado_pecas=enviado,
        envios_sem_numero=sum(1 for e in envios if e.sem_numero),
        retornado_pecas=retornado,
        retalho_producao_kg=sum(retalho_vals) if retalho_vals else None,
        saldo_a_retornar=max(alvo - retornado, 0),
        status=status,
    )
    return resultado


@dataclass
class ProducaoAcumulada:
    """O que a facção apontou nesta OP, somando todos os dias lançados."""

    produzido_1a_total: int = 0
    produzido_2a_total: int = 0
    produzido_total: int = 0
    # None = ninguém pesou retalho nenhum ainda. Zero seria mentira: diria
    # "produziu sem gerar retalho", que é diferente de "não foi medido" — e
    # o balanço de material (Fase 4) depende dessa distinção.
    retalho_producao_kg_total: float | None = None
    # Peças que saíram da Zanattex e ainda não foram apontadas como
    # produzidas: o WIP do estágio Envio → Produção, ou seja, o que está
    # parado na facção agora.
    wip_envio_producao: int = 0
    apontamentos: int = 0

    @property
    def tem_apontamento(self) -> bool:
        return self.apontamentos > 0


def producao_por_op(programacao: ProgramacaoCorte, *, enviado_pecas: int | None = None
                    ) -> ProducaoAcumulada:
    """`enviado_pecas` opcional só pra reaproveitar a soma que
    `calcular_producao()` já fez — quando não vem, é recalculada aqui (usa o
    prefetch de `envios_producao`, então não custa query extra nas telas)."""
    registros = list(programacao.registros_producao.all())
    if enviado_pecas is None:
        enviado_pecas = sum(e.quantidade_pecas for e in programacao.envios_producao.all())

    primeira = sum(r.quantidade_pecas for r in registros)
    segunda = sum(r.qualidade_segunda_pecas for r in registros)
    retalho_vals = [float(r.retalho_kg) for r in registros if r.retalho_kg is not None]
    total = primeira + segunda

    return ProducaoAcumulada(
        produzido_1a_total=primeira,
        produzido_2a_total=segunda,
        produzido_total=total,
        retalho_producao_kg_total=sum(retalho_vals) if retalho_vals else None,
        wip_envio_producao=max(enviado_pecas - total, 0),
        apontamentos=len(registros),
    )


# Sentinel de "ninguém disse pra qual prestador era" — só aparece quando a OP
# já tem 2+ prestadores E ainda assim existe apontamento/retorno sem
# `destino` preenchido (dado lançado antes desta fase, ou lançado fora da
# tela que já exige a escolha). Rótulo, não valor real de destino — nunca
# escondido, sempre aparece como pendência na tela.
NAO_INFORMADO = "— não informado —"


@dataclass
class SaldoPrestador:
    """O mesmo recorte de `ProducaoOP`/`ProducaoAcumulada`, só que por
    prestador em vez de somado pra OP inteira — só faz diferença quando a OP
    foi dividida entre mais de um. `alvo`/`saldo_a_retornar` seguem a MESMA
    régua da Fase 3 (`calcular_producao`): fecha contra o que a facção
    apontou ter produzido, caindo pro enviado como provisório enquanto não
    há apontamento nenhum."""
    destino: str
    enviado_pecas: int = 0
    produzido_pecas: int = 0
    retornado_pecas: int = 0
    saldo_a_retornar: int = 0


def saldo_por_prestador(programacao: ProgramacaoCorte) -> list[SaldoPrestador]:
    """Um item por prestador que já recebeu envio desta OP — na ordem em que
    cada um apareceu (mesma ordem de `_destinos_da_op` em controle_op/
    forms.py). Se houver apontamento/retorno sem `destino` preenchido
    enquanto a OP já tem 2+ prestadores, entra um item extra rotulado
    `NAO_INFORMADO` no final — pendência visível, não descartada em
    silêncio."""
    envios = list(programacao.envios_producao.all())
    producoes = list(programacao.registros_producao.all())
    retornos = list(programacao.retornos_producao.all())

    destinos: list[str] = []
    for e in envios:
        if e.destino and e.destino not in destinos:
            destinos.append(e.destino)

    resultado = []
    for destino in destinos:
        enviado = sum(e.quantidade_pecas for e in envios if e.destino == destino)
        produzido = sum(
            r.quantidade_pecas + r.qualidade_segunda_pecas
            for r in producoes if r.destino == destino)
        retornado = sum(r.quantidade_pecas for r in retornos if r.destino == destino)
        alvo = produzido or enviado or 0
        resultado.append(SaldoPrestador(
            destino=destino, enviado_pecas=enviado, produzido_pecas=produzido,
            retornado_pecas=retornado, saldo_a_retornar=max(alvo - retornado, 0),
        ))

    if len(destinos) > 1:
        produzido_orfao = sum(
            r.quantidade_pecas + r.qualidade_segunda_pecas
            for r in producoes if not r.destino)
        retornado_orfao = sum(r.quantidade_pecas for r in retornos if not r.destino)
        if produzido_orfao or retornado_orfao:
            resultado.append(SaldoPrestador(
                destino=NAO_INFORMADO, produzido_pecas=produzido_orfao,
                retornado_pecas=retornado_orfao,
                saldo_a_retornar=max(produzido_orfao - retornado_orfao, 0),
            ))

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
