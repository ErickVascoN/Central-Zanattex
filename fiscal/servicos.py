"""
Consultas e agregações do Saldo Fiscal — mantém views.py fino. Nada aqui
depende de HttpRequest; filtros chegam já normalizados (ver `Filtros`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Q, QuerySet, Sum
from django.db.models.functions import TruncMonth

from .models import Cliente, NotaFiscal, NotaFiscalItem, PendenciaMatching


@dataclass
class Filtros:
    centro_custo: str = ""
    busca_nota: str = ""
    data_inicio: date | None = None
    data_fim: date | None = None

    @property
    def ativo(self) -> bool:
        return bool(self.centro_custo or self.busca_nota or self.data_inicio or self.data_fim)


def filtros_da_query(get) -> Filtros:
    """Lê os filtros comuns de Relatórios/Histórico a partir de
    `request.GET` — centro de custo, busca por número/chave de nota, e
    período (data inicial/final)."""
    def _data(chave: str) -> date | None:
        valor = get.get(chave, "")
        try:
            return datetime.strptime(valor, "%Y-%m-%d").date() if valor else None
        except ValueError:
            return None

    return Filtros(
        centro_custo=get.get("centro_custo", "").strip(),
        busca_nota=get.get("busca_nota", "").strip(),
        data_inicio=_data("data_inicio"),
        data_fim=_data("data_fim"),
    )


def aplicar_filtros(qs: QuerySet[NotaFiscal], filtros: Filtros) -> QuerySet[NotaFiscal]:
    if filtros.centro_custo:
        qs = qs.filter(centro_custo=filtros.centro_custo)
    if filtros.busca_nota:
        qs = qs.filter(
            Q(n_nf__icontains=filtros.busca_nota) | Q(chave_acesso__icontains=filtros.busca_nota))
    if filtros.data_inicio:
        qs = qs.filter(data_emissao__date__gte=filtros.data_inicio)
    if filtros.data_fim:
        qs = qs.filter(data_emissao__date__lte=filtros.data_fim)
    return qs


def opcoes_centro_custo() -> list[str]:
    return list(
        NotaFiscal.objects.exclude(centro_custo="")
        .order_by("centro_custo").values_list("centro_custo", flat=True).distinct())


@dataclass
class Kpis:
    recebido_total: Decimal
    saldo_total: Decimal
    pct_consumo: float
    nfs_pendentes: int
    pendencias_abertas: int
    clientes_ativos: int


def kpis_dashboard() -> Kpis:
    entradas = NotaFiscalItem.objects.filter(nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA)
    agregado = entradas.aggregate(recebido=Sum("q_com"), saldo=Sum("saldo_atual"))
    recebido = agregado["recebido"] or Decimal("0")
    saldo = agregado["saldo"] or Decimal("0")
    consumido = recebido - saldo
    pct_consumo = float(consumido / recebido * 100) if recebido else 0.0
    return Kpis(
        recebido_total=recebido,
        saldo_total=saldo,
        pct_consumo=round(pct_consumo, 1),
        nfs_pendentes=NotaFiscal.objects.filter(status=NotaFiscal.Status.PENDENTE).count(),
        pendencias_abertas=PendenciaMatching.objects.filter(resolvido=False).count(),
        clientes_ativos=Cliente.objects.filter(ativo=True).count(),
    )


def evolucao_mensal() -> dict:
    """Quantidade recebida x devolvida por mês — dados prontos pro gráfico
    de linha do Início (Plotly no template)."""
    linhas = (
        NotaFiscalItem.objects
        .annotate(mes=TruncMonth("nota_fiscal__data_emissao"))
        .values("mes", "nota_fiscal__tipo")
        .annotate(total=Sum("q_com"))
        .order_by("mes")
    )
    meses = sorted({l["mes"] for l in linhas})
    por_tipo = {"ENTRADA": {}, "SAIDA": {}}
    for l in linhas:
        por_tipo[l["nota_fiscal__tipo"]][l["mes"]] = float(l["total"] or 0)
    return {
        "meses": [m.strftime("%m/%Y") for m in meses],
        "entrada": [por_tipo["ENTRADA"].get(m, 0) for m in meses],
        "saida": [por_tipo["SAIDA"].get(m, 0) for m in meses],
    }


def saldo_por_centro_custo() -> dict:
    linhas = (
        NotaFiscalItem.objects
        .filter(nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA)
        .values("nota_fiscal__centro_custo")
        .annotate(saldo=Sum("saldo_atual"))
        .order_by("-saldo")
    )
    return {
        "rotulos": [l["nota_fiscal__centro_custo"] or "Não informado" for l in linhas],
        "valores": [float(l["saldo"] or 0) for l in linhas],
    }


@dataclass
class CarteiraNota:
    """Uma linha da 'carteira' de Histórico — a NF de entrada com seu %
    consumido, pronta pro expander da movimentação."""
    nota: NotaFiscal
    recebido: Decimal
    saldo: Decimal
    pct_consumido: float


def carteira_entradas(filtros: Filtros) -> list[CarteiraNota]:
    """Lista de NFs de entrada com saldo/percentual agregado — a 'carteira
    geral' pedida pro Histórico, cada linha expansível pra ver os
    vínculos de saída que baixaram dela (ver `movimentacao_da_nota`)."""
    qs = aplicar_filtros(
        NotaFiscal.objects.filter(tipo=NotaFiscal.Tipo.ENTRADA), filtros
    ).annotate(recebido=Sum("itens__q_com"), saldo=Sum("itens__saldo_atual"))

    linhas = []
    for nota in qs:
        recebido = nota.recebido or Decimal("0")
        saldo = nota.saldo or Decimal("0")
        pct = float((recebido - saldo) / recebido * 100) if recebido else 0.0
        linhas.append(CarteiraNota(nota=nota, recebido=recebido, saldo=saldo, pct_consumido=round(pct, 1)))
    return linhas


def movimentacao_da_nota(nota_entrada: NotaFiscal):
    """Todos os vínculos (baixas) que consumiram saldo dessa NF de entrada —
    o conteúdo do expander na tela de Histórico."""
    from .models import Vinculo
    return (
        Vinculo.objects
        .filter(entrada_item__nota_fiscal=nota_entrada)
        .select_related("saida_item", "saida_item__nota_fiscal", "entrada_item")
        .order_by("-criado_em")
    )


def saldo_por_produto(filtros: Filtros):
    """Base do relatório de saldo — um item de entrada por linha, com
    quantidade recebida, saldo atual e % consumido."""
    qs = aplicar_filtros(
        NotaFiscal.objects.filter(tipo=NotaFiscal.Tipo.ENTRADA), filtros
    )
    return (
        NotaFiscalItem.objects
        .filter(nota_fiscal__in=qs)
        .select_related("nota_fiscal", "nota_fiscal__cliente")
        .order_by("nota_fiscal__cliente__nome", "x_prod")
    )
