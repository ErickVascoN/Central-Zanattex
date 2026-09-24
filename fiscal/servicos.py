"""
Consultas e agregações do Saldo Fiscal — mantém views.py fino. Nada aqui
depende de HttpRequest; filtros chegam já normalizados (ver `Filtros`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Q, QuerySet, Sum
from django.db.models.functions import TruncMonth

from . import referencia
from .models import Cliente, NotaFiscal, NotaFiscalItem, PendenciaMatching, formatar_cnpj


@dataclass
class Filtros:
    centro_custo: str = ""
    busca_nota: str = ""
    data_inicio: date | None = None
    data_fim: date | None = None
    # Vazio = comportamento de sempre (Histórico por entrada só mostra
    # VALIDA; por saída mostra tudo) — só filtra por uma Situacao específica
    # quando escolhida explicitamente (ver saldo_por_produto/
    # historico_saida_itens). É o que dá visibilidade pra nota que já nasce
    # CANCELADA no import (ver fiscal/importador.py) — sem isso ela nunca
    # aparecia em lugar nenhum do Histórico se fosse uma NF de entrada.
    situacao: str = ""

    @property
    def ativo(self) -> bool:
        return bool(self.centro_custo or self.busca_nota or self.data_inicio or self.data_fim or self.situacao)


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
        situacao=get.get("situacao", "").strip(),
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


def rotulos_centro_custo() -> dict[str, str]:
    """centro_custo (CNPJ da Zanattex na nota) -> "Razão social — CNPJ".
    O CNPJ fica sempre no rótulo: duas unidades podem ter o mesmo nome, e
    são centros de custo diferentes. A razão social vem da nota mais recente
    daquele CNPJ (lado Zanattex: emitente na saída, destinatário na entrada)."""
    rotulos: dict[str, str] = {}
    notas = (
        NotaFiscal.objects.exclude(centro_custo="")
        .order_by("-data_emissao").values_list("centro_custo", "tipo", "emit_nome", "dest_nome"))
    for cc, tipo, emit_nome, dest_nome in notas:
        if cc in rotulos:
            continue
        nome = emit_nome if tipo == NotaFiscal.Tipo.SAIDA else dest_nome
        rotulos[cc] = f"{nome} — {formatar_cnpj(cc)}" if nome else formatar_cnpj(cc)
    return rotulos


def rotulo_centro_custo(cc: str) -> str:
    return rotulos_centro_custo().get(cc, formatar_cnpj(cc)) if cc else ""


def opcoes_centro_custo() -> list[tuple[str, str]]:
    """(valor, rótulo) pro <select> — valor é o CNPJ, ordenado pelo rótulo."""
    return sorted(rotulos_centro_custo().items(), key=lambda par: par[1])


def contar_pendencias_abertas() -> int:
    """Só a contagem — usado no aviso da sidebar (ver fiscal/views.py::_contexto),
    carregado em toda página do módulo, por isso fica separado do resto dos
    KPIs do Início (que agregam bem mais coisa e não precisam rodar sempre)."""
    return PendenciaMatching.objects.filter(resolvido=False).count()


def _tecido_das_entradas():
    """Itens de tecido controlado de entradas VALIDA — a base de todo total
    de saldo. `saldo_atual` preenchido = item rastreado (etiqueta, caixa e
    nota anulada/sem autorização ficam de fora)."""
    return NotaFiscalItem.objects.filter(
        nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA,
        nota_fiscal__situacao=NotaFiscal.Situacao.VALIDA,
        saldo_atual__isnull=False,
    )


@dataclass
class TotalUnidade:
    """Totais de uma unidade (KG ou MT) — nunca se soma quilo com metro."""
    unidade: str
    recebido: Decimal = Decimal("0")
    utilizado: Decimal = Decimal("0")
    saldo: Decimal = Decimal("0")      # só o que ainda sobra (itens com saldo > 0)
    excedido: Decimal = Decimal("0")   # devolvido além do recebido (itens com saldo < 0)

    @property
    def pct_utilizado(self) -> float:
        return round(float(self.utilizado / self.recebido * 100), 1) if self.recebido else 0.0


def totais_por_unidade(itens) -> list[TotalUnidade]:
    """Soma recebido/utilizado/saldo/excedido por unidade normalizada (Kg e
    KG juntos). `itens` são NotaFiscalItem de entrada com saldo_atual."""
    totais: dict[str, TotalUnidade] = {}
    for item in itens:
        un = referencia.normalizar_unidade(item.u_com)
        t = totais.setdefault(un, TotalUnidade(un))
        saldo = item.saldo_atual or Decimal("0")
        t.recebido += item.q_com
        t.utilizado += item.q_com - saldo
        if saldo > 0:
            t.saldo += saldo
        else:
            t.excedido += -saldo
    return sorted(totais.values(), key=lambda t: t.unidade)


@dataclass
class Kpis:
    por_unidade: list[TotalUnidade]
    nfs_pendentes: int
    pendencias_abertas: int
    clientes_ativos: int


def cards_totais(por_unidade: list[TotalUnidade], valores_rs: dict[str, Decimal] | None = None) -> list[dict]:
    """Os 4 cards padrão (Entrada/Utilizado/Saldo/Excedência), cada um com a
    quantidade por unidade (KG e MT nunca somados na mesma linha) — usado
    tanto no Início quanto no Histórico (`fiscal-kpis fiscal-kpis-4` no
    template), pra não ter dois jeitos diferentes de mostrar a mesma coisa
    (achado do usuário: o Início antes tinha 1 card por combinação
    métrica×unidade, virava uma parede de blocos pequenos). `valores_rs`
    (opcional) é o total em R$ de cada card — quando não vem, o template
    simplesmente não mostra o rodapé de valor."""
    valores_rs = valores_rs or {}
    campos = [("recebido", "Entrada"), ("utilizado", "Utilizado"), ("saldo", "Saldo"), ("excedido", "Excedência")]
    return [
        {
            "rotulo": rotulo,
            "alerta": campo == "excedido" and any(getattr(t, campo) for t in por_unidade),
            "linhas": [(t.unidade, getattr(t, campo)) for t in por_unidade],
            "valor": valores_rs.get(campo),
        }
        for campo, rotulo in campos
    ]


def kpis_dashboard() -> Kpis:
    return Kpis(
        por_unidade=totais_por_unidade(_tecido_das_entradas().only("u_com", "q_com", "saldo_atual")),
        nfs_pendentes=NotaFiscal.objects.filter(status=NotaFiscal.Status.PENDENTE).count(),
        pendencias_abertas=PendenciaMatching.objects.filter(resolvido=False).count(),
        clientes_ativos=Cliente.objects.filter(ativo=True).count(),
    )


def evolucao_mensal() -> dict:
    """Tecido recebido x devolvido (usado/perdido/retornado) por mês e por
    unidade — só tecido controlado de notas VALIDA (sem etiqueta, produto
    acabado nem nota anulada). Dados prontos pro gráfico do Início."""
    serie: dict[tuple[str, str], dict] = {}

    def somar(qs, tipo):
        linhas = (qs.annotate(mes=TruncMonth("nota_fiscal__data_emissao"))
                  .values("mes", "u_com").annotate(total=Sum("q_com")))
        for l in linhas:
            chave = (tipo, referencia.normalizar_unidade(l["u_com"]))
            serie.setdefault(chave, {})
            serie[chave][l["mes"]] = serie[chave].get(l["mes"], 0) + float(l["total"] or 0)

    somar(_tecido_das_entradas(), "Recebido")
    devolvidos = NotaFiscalItem.objects.filter(
        nota_fiscal__tipo=NotaFiscal.Tipo.SAIDA, nota_fiscal__situacao=NotaFiscal.Situacao.VALIDA,
        tipo_retorno=NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO,
        ncm__in=settings.FISCAL_NCMS_CONTROLADOS)
    somar(devolvidos, "Devolvido")
    meses = sorted({m for valores in serie.values() for m in valores})
    return {
        "meses": [m.strftime("%m/%Y") for m in meses],
        "series": [
            {"nome": f"{tipo} ({un})", "tipo": tipo, "unidade": un,
             "valores": [round(valores.get(m, 0), 2) for m in meses]}
            for (tipo, un), valores in sorted(serie.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        ],
    }


def saldo_por_centro_custo() -> dict:
    """Saldo que sobra (itens com saldo > 0) por centro de custo, uma série
    por unidade — excedido de uma NF não abate a sobra de outra aqui."""
    rotulos = rotulos_centro_custo()
    valores: dict[str, dict[str, float]] = {}
    for cc, un, saldo in _tecido_das_entradas().filter(saldo_atual__gt=0).values_list(
            "nota_fiscal__centro_custo", "u_com", "saldo_atual"):
        unidade = referencia.normalizar_unidade(un)
        valores.setdefault(unidade, {})
        valores[unidade][cc] = valores[unidade].get(cc, 0) + float(saldo)
    centros = sorted({cc for v in valores.values() for cc in v})
    return {
        "rotulos": [rotulos.get(cc, cc) or "Não informado" for cc in centros],
        "series": [{"unidade": un, "valores": [round(v.get(cc, 0), 2) for cc in centros]}
                   for un, v in sorted(valores.items())],
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
# Rótulos curtos pra NF fora de VALIDA (ver saldo_por_produto/
# classificar_status_entrada) — deliberadamente FORA de STATUS_HISTORICO:
# essa fila de chips é sobre progresso de consumo, o filtro de Situação (ver
# filtros_da_query) já cobre isso de outro jeito; misturar os dois deixava a
# tela ambígua (achado do usuário — duas UIs mostrando a mesma informação).
_ROTULOS_SITUACAO_STATUS = {
    NotaFiscal.Situacao.CANCELADA: "Cancelada",
    NotaFiscal.Situacao.EXCLUIDA: "Excluída do saldo",
    NotaFiscal.Situacao.ANULADA: "Anulada",
    NotaFiscal.Situacao.NAO_AUTORIZADA: "Sem autorização",
    NotaFiscal.Situacao.ESTORNO: "Estorno",
}


def rotulo_status_historico(status: str) -> str:
    return STATUS_HISTORICO.get(status) or _ROTULOS_SITUACAO_STATUS.get(status, status)


# Opções curtas pro <select> de Situação do Histórico/Relatórios — não reusa
# NotaFiscal.Situacao.choices direto: os rótulos do model são longos
# (pensados pro admin/tooltip, não pra um <select> compacto), e VALIDA nem
# entra na lista — já é o padrão implícito da opção vazia "Válida (padrão)".
SITUACAO_CHOICES_FILTRO = [
    (NotaFiscal.Situacao.CANCELADA, "Cancelada"),
    (NotaFiscal.Situacao.EXCLUIDA, "Excluída do saldo"),
    (NotaFiscal.Situacao.ANULADA, "Anulada (estorno)"),
    (NotaFiscal.Situacao.NAO_AUTORIZADA, "Sem autorização"),
    (NotaFiscal.Situacao.ESTORNO, "Estorno (entrada própria)"),
]


def classificar_status_entrada(item: NotaFiscalItem, tem_pendencia: bool) -> str:
    """Status de um item de entrada (ver STATUS_HISTORICO) — usado tanto na
    listagem quanto no modal de detalhe, pra não divergirem.

    Nota fora de VALIDA (só acontece quando filtros.situacao pede
    explicitamente, ver saldo_por_produto) nunca teve saldo de verdade —
    devolve a própria Situacao em vez de calcular consumo/excesso, que não
    fazem sentido pra ela (senão uma nota cancelada apareceria como "100%
    utilizada", o oposto do que aconteceu)."""
    if item.nota_fiscal.situacao != NotaFiscal.Situacao.VALIDA:
        return item.nota_fiscal.situacao
    saldo_bruto = item.saldo_atual if item.saldo_atual is not None else Decimal("0")
    utilizado = item.q_com - saldo_bruto
    # Excedido vem antes: saldo negativo é fato medido, e a própria baixa a
    # mais abre uma pendência QUANTIDADE_EXCEDIDA — se divergência viesse
    # primeiro, nenhum item chegaria a "Excedido".
    if saldo_bruto < 0:
        return "EXCEDIDO"
    if tem_pendencia:
        return "DIVERGENCIA"
    if utilizado <= 0:
        return "NAO_UTILIZADO"
    if saldo_bruto == 0:
        return "TOTAL"
    return "PARCIAL"


def _pendencias_de_divergencia():
    """Pendências abertas que contam como "Com divergência" — excesso de
    saldo fica de fora, ele já aparece como "Excedido"."""
    return PendenciaMatching.objects.filter(resolvido=False).exclude(
        motivo=PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA)


def tem_pendencia_aberta(item: NotaFiscalItem) -> bool:
    """Só o item afetado (PendenciaMatching.itens_entrada), não a NF toda —
    os outros itens da mesma NF seguem com o próprio status."""
    return _pendencias_de_divergencia().filter(itens_entrada=item).exists()


def historico_itens(filtros: Filtros) -> list[ItemHistorico]:
    """Um item de entrada por linha, com status classificado (não
    utilizado/parcial/total/excedido/divergência). "Com divergência" só no
    item ligado à pendência (PendenciaMatching.itens_entrada); pendência
    que não sabe o item (ex.: SEM_REFERENCIA) fica só na tela Pendências."""
    itens = list(saldo_por_produto(filtros).prefetch_related("vinculos_entrada"))
    itens_divergentes = set(
        _pendencias_de_divergencia().filter(itens_entrada__isnull=False)
        .values_list("itens_entrada", flat=True))

    resultado = []
    for item in itens:
        q_com = item.q_com
        status = classificar_status_entrada(item, item.pk in itens_divergentes)
        if item.nota_fiscal.situacao != NotaFiscal.Situacao.VALIDA:
            # Nunca teve saldo de verdade — 0 em tudo em vez de "100%
            # utilizado" (que seria o cálculo padrão com saldo_atual=None,
            # ver classificar_status_entrada).
            utilizado = excedido = saldo = Decimal("0")
            pct = 0.0
        else:
            saldo_bruto = item.saldo_atual if item.saldo_atual is not None else Decimal("0")
            utilizado = q_com - saldo_bruto
            excedido = -saldo_bruto if saldo_bruto < 0 else Decimal("0")
            saldo = max(saldo_bruto, Decimal("0"))
            pct = float(utilizado / q_com * 100) if q_com else 0.0

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


def historico_saida_itens(filtros: Filtros, *, mostrar_insumos: bool = False) -> list[SaidaHistorico]:
    """`mostrar_insumos=False` (padrão): só tecido controlado (ver
    eh_ncm_controlado) — sem isso a tela mistura tecido com etiqueta/
    embalagem/outros insumos que usam o mesmo CFOP de devolução mas nunca
    casam com nada (linha inteira em "—"), poluindo a tabela à toa (achado
    do usuário). `mostrar_insumos=True` tira esse filtro."""
    from .models import Vinculo

    qs = NotaFiscal.objects.filter(tipo=NotaFiscal.Tipo.SAIDA)
    if filtros.situacao:
        qs = qs.filter(situacao=filtros.situacao)
    qs = aplicar_filtros(qs, filtros)
    itens = (
        NotaFiscalItem.objects
        .filter(nota_fiscal__in=qs, tipo_retorno=NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO)
        .select_related("nota_fiscal", "nota_fiscal__cliente", "nota_fiscal__anulada_por")
        .prefetch_related("vinculos_saida__entrada_item__nota_fiscal", "pendencias")
        .order_by("-nota_fiscal__data_emissao")
    )
    if not mostrar_insumos:
        itens = itens.filter(ncm__in=settings.FISCAL_NCMS_CONTROLADOS)
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


def pendencias_da_entrada(entrada_item: NotaFiscalItem):
    """Pendências abertas ligadas a esse item de entrada (inclusive o
    excesso) — a lista do modal de detalhe do Histórico."""
    return (
        PendenciaMatching.objects.filter(resolvido=False, itens_entrada=entrada_item)
        .select_related("saida_item", "saida_item__nota_fiscal")
    )


def saldo_por_produto(filtros: Filtros):
    """Base do relatório de saldo — um item de entrada por linha, com
    quantidade recebida, saldo atual e % consumido. Só itens com
    `saldo_atual` preenchido (NCM dentro do escopo controlado, ver
    eh_ncm_controlado) — item fora do escopo tem `saldo_atual=None`
    (nunca foi rastreado), bem diferente de "saldo zerado", e não deve
    aparecer aqui como se estivesse 100% consumido.

    Sem `filtros.situacao`, só VALIDA (comportamento de sempre — é o saldo
    "de verdade"). Com uma Situacao escolhida explicitamente (ex.:
    CANCELADA), mostra só essa — é assim que uma nota de entrada cancelada
    no import fica visível no Histórico (do contrário nunca apareceria: sem
    saldo_atual preenchido, filtrada tanto pela situacao quanto por não ter
    saldo rastreado)."""
    situacao = filtros.situacao or NotaFiscal.Situacao.VALIDA
    qs = aplicar_filtros(NotaFiscal.objects.filter(tipo=NotaFiscal.Tipo.ENTRADA, situacao=situacao), filtros)
    itens = NotaFiscalItem.objects.filter(nota_fiscal__in=qs)
    if not filtros.situacao:
        # Só filtra por "rastreado" no caso padrão (saldo de verdade) — uma
        # nota cancelada nunca teve saldo_atual preenchido (ver
        # fiscal/importador.py::confirmar_importacao_parsed), então exigir
        # isso quando o usuário pediu explicitamente pra ver as canceladas
        # esconderia elas de novo.
        itens = itens.filter(saldo_atual__isnull=False)
    return itens.select_related("nota_fiscal", "nota_fiscal__cliente").order_by(
        "nota_fiscal__cliente__nome", "x_prod")


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
        # Unidade entra na chave: o mesmo tecido em KG e em MT são dois
        # saldos diferentes, nunca se somam.
        chave = item.x_prod if agrupar_por == "descricao" else item.c_prod
        chave = f"{chave}|{referencia.normalizar_unidade(item.u_com)}"
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
            unidade=referencia.normalizar_unidade(lista[0].u_com), qtd_nfs=len({i.nota_fiscal_id for i in lista}),
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
    valor, _, unidade = chave.rpartition("|")
    itens = saldo_por_produto(filtros).filter(**{campo: valor or chave})
    return [i for i in itens if not unidade or referencia.normalizar_unidade(i.u_com) == unidade]
