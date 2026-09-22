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


def contar_pendencias_abertas() -> int:
    """Só a contagem — usado no aviso da sidebar (ver fiscal/views.py::_contexto),
    carregado em toda página do módulo, por isso fica separado do resto dos
    KPIs do Início (que agregam bem mais coisa e não precisam rodar sempre)."""
    return PendenciaMatching.objects.filter(resolvido=False).count()


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
class ItemHistorico:
    """Uma linha do Histórico — o item de entrada com quanto já foi
    usado/perdido, quanto ainda resta (o foco é sempre KG/MT — o tecido
    físico), o status (ver STATUS_HISTORICO) e o valor pelo custo da
    entrada, como comparativo (igual ao protótipo do fiscal: mesma tela que
    ele chamava de Conciliação — viraram uma só)."""
    item: NotaFiscalItem
    utilizado: Decimal
    saldo: Decimal
    excedido: Decimal
    pct_utilizado: float
    status: str
    qtd_saidas: int
    valor_entrada: Decimal
    valor_utilizado: Decimal
    valor_saldo: Decimal


STATUS_HISTORICO = {
    "NAO_UTILIZADO": "Não utilizado",
    "PARCIAL": "Parcialmente utilizado",
    "TOTAL": "Totalmente utilizado",
    "EXCEDIDO": "Excedido",
    "DIVERGENCIA": "Com divergência",
}


def classificar_status_entrada(item: NotaFiscalItem, tem_pendencia: bool) -> str:
    """Status de um item de entrada (ver STATUS_HISTORICO) — usado tanto na
    listagem quanto no modal de detalhe, pra não divergirem."""
    saldo_bruto = item.saldo_atual if item.saldo_atual is not None else Decimal("0")
    utilizado = item.q_com - saldo_bruto
    if tem_pendencia:
        return "DIVERGENCIA"
    if saldo_bruto < 0:
        return "EXCEDIDO"
    if utilizado <= 0:
        return "NAO_UTILIZADO"
    if saldo_bruto == 0:
        return "TOTAL"
    return "PARCIAL"


def tem_pendencia_aberta(nota: NotaFiscal) -> bool:
    marcador = f"NF {nota.n_nf}"
    return PendenciaMatching.objects.filter(resolvido=False, detalhe__contains=marcador).exists()


def historico_itens(filtros: Filtros) -> list[ItemHistorico]:
    """Um item de entrada por linha, com status classificado (não
    utilizado/parcial/total/excedido/divergência). `tem_pendencia` é uma
    checagem por texto (as mensagens de pendência sempre citam
    "NF {número}") — não é um FK direto porque pendências não resolvidas
    nem sempre sabem qual é a entrada (ex.: SEM_REFERENCIA)."""
    itens = list(saldo_por_produto(filtros).prefetch_related("vinculos_entrada"))
    detalhes_pendentes = list(
        PendenciaMatching.objects.filter(resolvido=False).values_list("detalhe", flat=True))

    resultado = []
    for item in itens:
        q_com = item.q_com
        saldo_bruto = item.saldo_atual if item.saldo_atual is not None else Decimal("0")
        utilizado = q_com - saldo_bruto
        excedido = -saldo_bruto if saldo_bruto < 0 else Decimal("0")
        saldo = max(saldo_bruto, Decimal("0"))
        pct = float(utilizado / q_com * 100) if q_com else 0.0

        marcador = f"NF {item.nota_fiscal.n_nf}"
        tem_pendencia = any(marcador in d for d in detalhes_pendentes)
        status = classificar_status_entrada(item, tem_pendencia)

        resultado.append(ItemHistorico(
            item=item, utilizado=utilizado, saldo=saldo, excedido=excedido,
            pct_utilizado=round(pct, 1), status=status, qtd_saidas=len(item.vinculos_entrada.all()),
            valor_entrada=q_com * item.v_un_com, valor_utilizado=utilizado * item.v_un_com,
            valor_saldo=saldo * item.v_un_com,
        ))
    return resultado


@dataclass
class SaidaHistorico:
    """Visão "por item de saída" do Histórico (alternativa à "por NF de
    entrada") — cada item de devolução/perda, se já foi vinculado (e por
    qual critério) ou ainda está pendente."""
    item: NotaFiscalItem
    vinculo: object | None  # Vinculo, sem import direto pra não formar ciclo
    pendencia: object | None  # PendenciaMatching em aberto, se houver


def historico_saida_itens(filtros: Filtros) -> list[SaidaHistorico]:
    from .models import Vinculo

    qs = aplicar_filtros(NotaFiscal.objects.filter(tipo=NotaFiscal.Tipo.SAIDA), filtros)
    itens = (
        NotaFiscalItem.objects
        .filter(nota_fiscal__in=qs, tipo_retorno=NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO)
        .select_related("nota_fiscal", "nota_fiscal__cliente")
        .prefetch_related("vinculos_saida__entrada_item__nota_fiscal", "pendencias")
        .order_by("-nota_fiscal__data_emissao")
    )
    resultado = []
    for item in itens:
        vinculo = next(iter(item.vinculos_saida.all()), None)
        pendencia = next((p for p in item.pendencias.all() if not p.resolvido), None)
        resultado.append(SaidaHistorico(item=item, vinculo=vinculo, pendencia=pendencia))
    return resultado


def vinculos_do_item(entrada_item: NotaFiscalItem):
    """Baixas aplicadas contra esse item de entrada específico — o "rolo" e
    a tabela "Notas de saída relacionadas" do Histórico."""
    return (
        entrada_item.vinculos_entrada
        .select_related("saida_item", "saida_item__nota_fiscal")
        .order_by("-criado_em")
    )


def pendencias_da_entrada(nota_entrada: NotaFiscal):
    marcador = f"NF {nota_entrada.n_nf}"
    return (
        PendenciaMatching.objects.filter(resolvido=False, detalhe__contains=marcador)
        .select_related("saida_item", "saida_item__nota_fiscal")
    )


def saldo_por_produto(filtros: Filtros):
    """Base do relatório de saldo — um item de entrada por linha, com
    quantidade recebida, saldo atual e % consumido. Só itens com
    `saldo_atual` preenchido (NCM dentro do escopo controlado, ver
    eh_ncm_controlado) — item fora do escopo tem `saldo_atual=None`
    (nunca foi rastreado), bem diferente de "saldo zerado", e não deve
    aparecer aqui como se estivesse 100% consumido."""
    qs = aplicar_filtros(
        NotaFiscal.objects.filter(tipo=NotaFiscal.Tipo.ENTRADA), filtros
    )
    return (
        NotaFiscalItem.objects
        .filter(nota_fiscal__in=qs, saldo_atual__isnull=False)
        .select_related("nota_fiscal", "nota_fiscal__cliente")
        .order_by("nota_fiscal__cliente__nome", "x_prod")
    )


@dataclass
class ProdutoSaldo:
    """Uma linha da tela "Saldo de Tecidos" — o mesmo produto agrupado
    entre TODAS as NFs de entrada (cruzando clientes), diferente do
    Histórico (que é por NF). `chave` é o valor usado pro agrupamento
    (descrição ou código), pra reabrir o detalhe."""
    chave: str
    descricao: str
    codigos: list[str]
    unidade: str
    qtd_nfs: int
    recebido: Decimal
    utilizado: Decimal
    saldo: Decimal
    valor_recebido: Decimal
    valor_saldo: Decimal
    pct_utilizado: float


def saldo_por_tecido(
    filtros: Filtros, agrupar_por: str = "descricao", so_com_saldo: bool = False,
) -> list[ProdutoSaldo]:
    """Consolidado por produto — soma todas as NFs de entrada do mesmo
    tecido (por descrição ou por código, configurável), não importa de
    qual cliente. `agrupar_por` "descricao" agrupa por `x_prod`; "codigo"
    agrupa por `c_prod` (útil quando o mesmo tecido vem com descrições
    ligeiramente diferentes entre clientes)."""
    itens = list(saldo_por_produto(filtros))
    grupos: dict[str, list[NotaFiscalItem]] = {}
    for item in itens:
        chave = item.x_prod if agrupar_por == "descricao" else item.c_prod
        grupos.setdefault(chave, []).append(item)

    resultado = []
    for chave, lista in grupos.items():
        recebido = sum((i.q_com for i in lista), Decimal("0"))
        saldo_bruto = sum((i.saldo_atual or Decimal("0") for i in lista), Decimal("0"))
        saldo = max(saldo_bruto, Decimal("0"))
        utilizado = recebido - saldo_bruto
        pct = float(utilizado / recebido * 100) if recebido else 0.0
        valor_recebido = sum((i.q_com * i.v_un_com for i in lista), Decimal("0"))
        valor_saldo = sum(
            (max(i.saldo_atual or Decimal("0"), Decimal("0")) * i.v_un_com for i in lista), Decimal("0"))
        if so_com_saldo and saldo <= 0:
            continue
        resultado.append(ProdutoSaldo(
            chave=chave, descricao=lista[0].x_prod, codigos=sorted({i.c_prod for i in lista}),
            unidade=lista[0].u_com, qtd_nfs=len({i.nota_fiscal_id for i in lista}),
            recebido=recebido, utilizado=utilizado, saldo=saldo,
            valor_recebido=valor_recebido, valor_saldo=valor_saldo, pct_utilizado=round(pct, 1),
        ))
    resultado.sort(key=lambda p: p.saldo, reverse=True)
    return resultado


def resumo_saldo_por_unidade(produtos: list[ProdutoSaldo]) -> list[tuple[str, Decimal, Decimal]]:
    """[(unidade, saldo total, valor total)] — os chips de resumo no topo
    da tela Saldo de Tecidos."""
    agregados: dict[str, list[Decimal]] = {}
    for p in produtos:
        par = agregados.setdefault(p.unidade, [Decimal("0"), Decimal("0")])
        par[0] += p.saldo
        par[1] += p.valor_saldo
    return [(unidade, valores[0], valores[1]) for unidade, valores in agregados.items()]


def itens_do_tecido(filtros: Filtros, chave: str, agrupar_por: str) -> list[NotaFiscalItem]:
    """Todas as NFs de entrada desse produto (mesma chave de agrupamento) —
    conteúdo do modal de detalhe da tela Saldo de Tecidos."""
    campo = "x_prod" if agrupar_por == "descricao" else "c_prod"
    return list(saldo_por_produto(filtros).filter(**{campo: chave}))
