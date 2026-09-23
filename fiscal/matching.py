"""
O "consumo automático": casa um item de devolução/perda (NF de saída, CFOP
em fiscal/nfe_xml.py::CFOPS_DEVOLUCAO_INSUMO) com o item da NF de entrada de
onde ele está sendo baixado.

Porta pro Django a lógica de conciliação de um protótipo feito à parte por
um funcionário do fiscal (controle-tecidos-nfe-arquivo-unico-6.html — JS
local, IndexedDB), com uma diferença deliberada: aqui o NCM continua sendo
o 1º critério de casamento de item (é o campo padronizado pelo governo,
mais confiável que código interno ou descrição) — o resto da cascata dele
(associação aprendida, código citado em texto livre, único item da nota,
descrição idêntica, similaridade) entra como critérios seguintes, na mesma
ordem de prioridade que ele usava.

Regra de ouro, igual ao original: qualquer nível com 0 ou mais de 1
candidato NUNCA decide sozinho — vira PendenciaMatching pra revisão humana.
Fiscal é o tipo de dado que não pode errar em silêncio.

`encontrar_entrada_candidata` aceita tanto um `ItemParseado` (fiscal/nfe_xml.py,
usado na prévia antes de gravar nada) quanto um `NotaFiscalItem` já salvo
(usado na aplicação de verdade) — os dois expõem os mesmos atributos, então
a função não precisa saber qual é qual.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from . import referencia
from .models import AssociacaoProduto, NotaFiscal, NotaFiscalItem, PendenciaMatching, Vinculo
from .nfe_xml import eh_ncm_controlado


class ItemComReferencia(Protocol):
    ncm: str
    c_prod: str
    x_prod: str
    u_com: str
    inf_ad_prod: str
    tipo_retorno: str


@dataclass
class ResultadoCasamento:
    entrada_item: NotaFiscalItem | None
    motivo_pendencia: str | None
    detalhe: str = ""
    legenda: str = ""  # qual critério casou (NCM, código, associação...) — só informativo
    itens_afetados: list[NotaFiscalItem] = field(default_factory=list)  # entrada, quando se sabe

    @property
    def encontrado(self) -> bool:
        return self.entrada_item is not None

    @property
    def pendente(self) -> bool:
        return self.motivo_pendencia is not None


def _entradas_validas(cliente):
    """Só entrada VALIDA recebe baixa — anulada ou sem autorização não tem saldo."""
    return NotaFiscal.objects.filter(
        cliente=cliente, tipo=NotaFiscal.Tipo.ENTRADA, situacao=NotaFiscal.Situacao.VALIDA)


def _localizar_entrada_por_numero(cliente, numero: str) -> NotaFiscal | None:
    nota = _entradas_validas(cliente).filter(n_nf=numero).first()
    if nota:
        return nota
    # Fallback: número pode vir com formatação de zeros à esquerda diferente
    # da fonte original — compara os dois lados sem zeros à esquerda. Volume
    # de NFs de entrada por cliente é pequeno, então varrer é barato.
    numero_normalizado = numero.lstrip("0") or "0"
    for candidata in _entradas_validas(cliente):
        if (candidata.n_nf.lstrip("0") or "0") == numero_normalizado:
            return candidata
    return None


def _localizar_entrada_por_chave(cliente, chave: str) -> NotaFiscal | None:
    return _entradas_validas(cliente).filter(chave_acesso=chave).first()


def _resolver_nota_entrada(
    cliente, item: ItemComReferencia, inf_cpl: str, ref_nfe: list[str],
) -> tuple[NotaFiscal | None, str | None, str]:
    """Acha a NF de entrada citada, tentando em cadeia: infAdProd do item →
    descrição do item → informação complementar da nota inteira → NF-e
    formalmente referenciada. Só passa pra próxima fonte se a atual não
    achar NENHUMA nota real; se achar mais de uma, para ali."""
    fontes = [
        ("infAdProd", item.inf_ad_prod),
        ("descrição do item", item.x_prod),
        ("informação complementar da nota", inf_cpl),
    ]
    termo_sem_numero_seguro = False

    for nome_fonte, texto in fontes:
        referencias = referencia.extrair_referencias(texto)
        if not referencias:
            if referencia.contem_termo_referencia(texto):
                termo_sem_numero_seguro = True
            continue
        notas_achadas: list[NotaFiscal] = []
        for ref in referencias:
            nota = (_localizar_entrada_por_chave(cliente, ref.numero) if ref.eh_chave
                    else _localizar_entrada_por_numero(cliente, ref.numero))
            if nota and nota not in notas_achadas:
                notas_achadas.append(nota)
        if len(notas_achadas) == 1:
            return notas_achadas[0], None, ""
        if len(notas_achadas) > 1:
            nfs = ", ".join(n.n_nf for n in notas_achadas)
            return None, PendenciaMatching.Motivo.MULTIPLAS_NF, (
                f"Mais de uma NF de entrada citada na {nome_fonte}: {nfs}.")
        # Nenhuma nota encontrada nessa fonte — pode ser que ainda não foi
        # importada (aguardando) ou que a referência aponte pra outro lugar;
        # segue tentando as próximas fontes antes de desistir.

    for chave in ref_nfe:
        nota = _localizar_entrada_por_chave(cliente, chave)
        if nota:
            return nota, None, ""

    if not any(referencia.extrair_referencias(t) for _, t in fontes) and not ref_nfe:
        if termo_sem_numero_seguro:
            return None, PendenciaMatching.Motivo.REF_NAO_IDENTIFICADA, (
                'O texto cita algo como "NF"/"REF", mas nenhum número foi identificado com '
                "segurança — confira manualmente o infAdProd, a descrição do item e a "
                "informação complementar da nota.")
        return None, PendenciaMatching.Motivo.SEM_REFERENCIA, (
            "Nenhuma referência à NF de entrada encontrada — infAdProd, descrição do item, "
            "informação complementar da nota e NF-e referenciada estão vazios ou não citam uma NF.")

    return None, PendenciaMatching.Motivo.AGUARDANDO_ENTRADA, (
        "Referência à NF de entrada encontrada, mas nenhuma nota com esse número/chave foi "
        "importada ainda para este cliente — resolve sozinho assim que ela for importada.")


def _nivel(candidatos: list[NotaFiscalItem], entrada: NotaFiscal, legenda: str):
    """Um nível da cascata: devolve (item, legenda, None, []) se sobrou
    exatamente 1, (None, None, mensagem, candidatos) se ficou ambíguo (para
    ali, não tenta os próximos níveis), ou (None, None, None, []) se ficou
    vazio (tenta o próximo nível)."""
    if len(candidatos) == 1:
        return candidatos[0], legenda, None, []
    if len(candidatos) > 1:
        opcoes = "; ".join(f"item #{c.n_item} {c.x_prod} (saldo {c.saldo_atual})" for c in candidatos)
        return None, None, f"NF {entrada.n_nf}: mais de um item candidato por {legenda} — {opcoes}.", candidatos
    return None, None, None, []


def _cascata(base: list[NotaFiscalItem], todos: list[NotaFiscalItem], entrada: NotaFiscal,
             item: ItemComReferencia, cliente, *, permitir_unico: bool):
    """Os níveis de casamento, na ordem, sobre os itens de `base`. Para no
    primeiro nível que decide (1 candidato) ou fica ambíguo (vários)."""
    niveis = [
        ("NCM", lambda i: i.ncm == item.ncm),
        ("código do produto", lambda i: i.c_prod == item.c_prod),
    ]
    associacao = AssociacaoProduto.objects.filter(cliente=cliente, cprod_saida=item.c_prod).first()
    if associacao:
        niveis.append(("associação aprendida", lambda i: i.c_prod == associacao.cprod_entrada))
    if item.inf_ad_prod:
        niveis.append(("código citado no infAdProd", lambda i: bool(i.c_prod) and i.c_prod in item.inf_ad_prod))

    for legenda, criterio in niveis:
        resultado = _nivel([i for i in base if criterio(i)], entrada, legenda)
        if resultado[0] or resultado[2]:
            return resultado

    # "Único item com saldo" só vale se ele for de fato o único tecido da
    # NF: um item controlado já zerado, ou um tecido fora da lista de NCM
    # (sem saldo), não entram em `base` — e a devolução deles cairia aqui,
    # no item errado. Nesses casos vira pendência em vez de chute.
    if permitir_unico:
        controlados = [i for i in todos if i.saldo_atual is not None]
        tecido_sem_saldo = any(i.saldo_atual is None and referencia.parece_tecido(i.x_prod) for i in todos)
        if len(base) == 1 and len(controlados) == 1 and not tecido_sem_saldo:
            return base[0], "único item com saldo na NF", None, []

    resultado = _nivel(
        [i for i in base if referencia.descricoes_identicas(item.x_prod, i.x_prod)],
        entrada, "descrição idêntica")
    if resultado[0] or resultado[2]:
        return resultado
    return _nivel(
        [i for i in base if referencia.descricao_contida(item.x_prod, i.x_prod)
         or referencia.descricao_contida(i.x_prod, item.x_prod)],
        entrada, "descrição compatível")


def _resolver_item_entrada(entrada: NotaFiscal, item: ItemComReferencia, cliente):
    """Cascata de casamento dentro da NF de entrada já resolvida — NCM
    primeiro (nosso critério principal), depois a cascata do protótipo
    original: código do produto, associação aprendida, código citado em
    texto livre, único item com saldo na nota, descrição idêntica,
    descrição compatível (contida numa na outra).

    Roda primeiro só nos itens com saldo (quando a NF tem dois itens
    parecidos, prefere o que ainda tem). Se nenhum casar, roda de novo nos
    itens controlados já zerados: o produto certo pode ter acabado — aí a
    baixa vai nele mesmo e aparece como excedido, em vez de virar
    "produto sem correspondente" e sumir do saldo.

    Devolve (item, legenda, erro, candidatos) — `candidatos` são os itens
    de entrada envolvidos quando fica ambíguo."""
    todos = list(entrada.itens.all())
    com_saldo = [i for i in todos if i.saldo_atual is not None and i.saldo_atual > 0]
    zerados = [i for i in todos if i.saldo_atual is not None and i.saldo_atual <= 0]

    resultado = _cascata(com_saldo, todos, entrada, item, cliente, permitir_unico=True)
    if resultado[0] or resultado[2] or not zerados:
        return resultado
    resolvido, legenda, erro, candidatos = _cascata(
        zerados, todos, entrada, item, cliente, permitir_unico=False)
    if resolvido:
        return resolvido, f"{legenda} (item já sem saldo)", None, []
    return resolvido, legenda, erro, candidatos


def encontrar_entrada_candidata(
    cliente, item: ItemComReferencia, *, inf_cpl: str = "", ref_nfe: list[str] | None = None,
) -> ResultadoCasamento:
    """Só leitura — não grava nada. Usado na prévia da importação (pra
    mostrar o que vai casar antes do usuário confirmar) e na aplicação real
    (ver `aplicar_baixas_da_nota`)."""
    if item.tipo_retorno != NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO:
        return ResultadoCasamento(None, None)  # produto acabado ou item de entrada: não se aplica

    entrada, motivo, detalhe = _resolver_nota_entrada(cliente, item, inf_cpl, ref_nfe or [])
    if entrada is None:
        return ResultadoCasamento(None, motivo, detalhe)

    item_resolvido, legenda, erro_ambiguo, candidatos = _resolver_item_entrada(entrada, item, cliente)
    if erro_ambiguo:
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.MULTIPLOS_PRODUTOS, erro_ambiguo, itens_afetados=candidatos)
    if item_resolvido is None:
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.PRODUTO_SEM_CORRESPONDENTE,
            f'NF {entrada.n_nf}: nenhum item controlado corresponde '
            f'(NCM "{item.ncm}", código "{item.c_prod}").')

    if not referencia.unidades_compativeis(item.u_com, item_resolvido.u_com):
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.UNIDADE_INCOMPATIVEL,
            f'NF {entrada.n_nf} item #{item_resolvido.n_item}: saída em "{item.u_com}", entrada em '
            f'"{item_resolvido.u_com}" — confirme manualmente antes de baixar.',
            itens_afetados=[item_resolvido])

    return ResultadoCasamento(item_resolvido, None, legenda=legenda)


def criar_pendencia(saida_item: NotaFiscalItem, motivo: str, detalhe: str,
                    itens_entrada: list[NotaFiscalItem] | None = None) -> PendenciaMatching:
    """Cria a pendência já ligada aos itens de entrada afetados (quando se
    sabe quais são) — é o que marca "Com divergência"/"Excedido" só no item
    certo do Histórico, e não na NF inteira."""
    pendencia = PendenciaMatching.objects.create(saida_item=saida_item, motivo=motivo, detalhe=detalhe)
    if itens_entrada:
        pendencia.itens_entrada.set(itens_entrada)
    return pendencia


def aplicar_baixa(
    saida_item: NotaFiscalItem, entrada_item: NotaFiscalItem, criterio: str = "Resolução manual",
) -> str | None:
    """Cria o Vinculo e decrementa `saldo_atual` do item de entrada de forma
    atômica. Devolve um aviso quando a quantidade devolvida é maior que o
    saldo disponível — a baixa ainda é aplicada (o retorno físico
    aconteceu), só fica sinalizada pra revisão: saldo negativo é sempre
    sinal de um problema real (NF errada, entrada faltando). `criterio` vem
    de `ResultadoCasamento.legenda` quando é automático; o padrão
    "Resolução manual" cobre quem chama a partir de resolver_pendencia."""
    aviso = None
    if saida_item.q_com > entrada_item.saldo_atual:
        aviso = (
            f"Devolvido {saida_item.q_com} {saida_item.u_com}, saldo disponível era só "
            f"{entrada_item.saldo_atual} na NF {entrada_item.nota_fiscal.n_nf}.")
    with transaction.atomic():
        Vinculo.objects.create(
            saida_item=saida_item, entrada_item=entrada_item, quantidade_baixada=saida_item.q_com,
            criterio=criterio,
        )
        NotaFiscalItem.objects.filter(pk=entrada_item.pk).update(
            saldo_atual=F("saldo_atual") - saida_item.q_com)
    return aviso


def aplicar_baixas_da_nota(
    nota_saida: NotaFiscal, *, pular_itens: frozenset[int] = frozenset(),
    excesso_ja_revisado: frozenset[int] = frozenset(),
) -> int:
    """Roda o casamento pra todos os itens de devolução/perda de uma NF de
    saída já persistida (itens já gravados no banco). Chamado logo após o
    `bulk_create` dos itens, tanto no upload web quanto na carga em lote.
    Devolve a quantidade de pendências criadas; marca a NF como PENDENTE
    quando há pelo menos uma.

    Só nota VALIDA baixa — estorno, nota anulada/cancelada ou sem
    autorização de uso não mexem no saldo (ver NotaFiscal.Situacao).

    Itens de NCM fora de `FISCAL_NCMS_CONTROLADOS` (insumo de produção, não
    tecido) nem entram no casamento — v1 não controla esses, não é
    "pendência", é fora de escopo (ver eh_ncm_controlado).

    `pular_itens` / `excesso_ja_revisado` só são usados pelo
    `recalcular_baixas` (itens com decisão humana que não pode ser refeita)."""
    if nota_saida.situacao != NotaFiscal.Situacao.VALIDA:
        return 0
    pendencias_criadas = 0
    itens = nota_saida.itens.filter(tipo_retorno=NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO)
    for item in itens:
        if not eh_ncm_controlado(item.ncm) or item.pk in pular_itens:
            continue
        resultado = encontrar_entrada_candidata(
            nota_saida.cliente, item, inf_cpl=nota_saida.inf_cpl, ref_nfe=nota_saida.ref_nfe_lista)
        if resultado.encontrado:
            aviso = aplicar_baixa(item, resultado.entrada_item, criterio=resultado.legenda)
            if aviso and item.pk not in excesso_ja_revisado:
                criar_pendencia(item, PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA, aviso,
                                [resultado.entrada_item])
                pendencias_criadas += 1
        elif resultado.pendente:
            criar_pendencia(item, resultado.motivo_pendencia, resultado.detalhe, resultado.itens_afetados)
            pendencias_criadas += 1

    if pendencias_criadas:
        NotaFiscal.objects.filter(pk=nota_saida.pk).update(status=NotaFiscal.Status.PENDENTE)
    return pendencias_criadas


def atualizar_status(nota: NotaFiscal) -> None:
    """PENDENTE enquanto algum item da nota tiver pendência aberta, OK
    quando não tiver mais — chamado depois de resolver/desfazer."""
    aberta = PendenciaMatching.objects.filter(saida_item__nota_fiscal=nota, resolvido=False).exists()
    novo = NotaFiscal.Status.PENDENTE if aberta else NotaFiscal.Status.OK
    if nota.status != NotaFiscal.Status.ERRO and nota.status != novo:
        NotaFiscal.objects.filter(pk=nota.pk).update(status=novo)
        nota.status = novo


def desfazer_baixas_da_nota(nota: NotaFiscal) -> set[int]:
    """Tira da conta tudo que a nota mexeu no saldo: devolve ao item de
    entrada o que as saídas desta nota baixaram, apaga as pendências abertas
    dela e, se ela for uma ENTRADA, solta as saídas que tinham baixado
    nela. Devolve os ids das NFs de saída soltas — quem chama decide se
    rebaixa elas (numa entrada anulada, a baixa tem que ir pra outro
    lugar ou virar pendência)."""
    saidas_soltas: set[int] = set()
    with transaction.atomic():
        for v in Vinculo.objects.filter(saida_item__nota_fiscal=nota):
            NotaFiscalItem.objects.filter(pk=v.entrada_item_id).update(
                saldo_atual=F("saldo_atual") + v.quantidade_baixada)
            v.delete()
        PendenciaMatching.objects.filter(saida_item__nota_fiscal=nota, resolvido=False).delete()
        if nota.tipo == NotaFiscal.Tipo.ENTRADA:
            vinculos = Vinculo.objects.filter(entrada_item__nota_fiscal=nota)
            saidas_soltas = set(vinculos.values_list("saida_item__nota_fiscal_id", flat=True))
            vinculos.delete()
            PendenciaMatching.objects.filter(
                resolvido=False, saida_item__nota_fiscal_id__in=saidas_soltas).delete()
            nota.itens.update(saldo_atual=None)
    return saidas_soltas


def _assinatura(nota: NotaFiscal) -> Counter:
    """Itens da nota por (código, quantidade) — duas notas com a mesma
    assinatura têm exatamente os mesmos produtos nas mesmas quantidades."""
    return Counter((i.c_prod, i.q_com) for i in nota.itens.all())


def _notas_citadas_pelo_estorno(estorno: NotaFiscal) -> list[NotaFiscal]:
    """Candidatas a nota anulada: mesmo emitente, tpNF=1, citada pela chave
    (refNFe/infCpl) ou pelo número no infCpl ("ENTRADA REF NF 40235")."""
    base = (NotaFiscal.objects.filter(emit_cnpj=estorno.emit_cnpj, tp_nf="1")
            .exclude(pk=estorno.pk).prefetch_related("itens"))
    chaves = list(estorno.ref_nfe_lista)
    numeros = []
    for ref in referencia.extrair_referencias(estorno.inf_cpl):
        (chaves if ref.eh_chave else numeros).append(ref.numero)
    achadas = list(base.filter(chave_acesso__in=chaves)) if chaves else []
    for numero in numeros:
        for nota in base.filter(n_nf__regex=rf"^0*{numero}$"):
            if nota not in achadas:
                achadas.append(nota)
    return achadas


def aplicar_estorno(estorno: NotaFiscal) -> NotaFiscal | None:
    """Uma NF de entrada própria (tpNF=0) que cita outra nota do mesmo
    emitente com exatamente os mesmos itens e quantidades anula essa nota:
    ela vira ANULADA e o que ela mexeu no saldo é desfeito. Se a nota
    citada ainda não foi importada, abre ESTORNO_NAO_CONFERE (que se
    resolve sozinha quando ela chegar, ver aplicar_estornos_pendentes); se
    foi, mas os itens não batem, também — anular pela metade seria chute."""
    if estorno.situacao != NotaFiscal.Situacao.ESTORNO:
        return None
    PendenciaMatching.objects.filter(
        motivo=PendenciaMatching.Motivo.ESTORNO_NAO_CONFERE, resolvido=False,
        detalhe__startswith=f"[estorno {estorno.pk}]").delete()
    candidatas = _notas_citadas_pelo_estorno(estorno)
    assinatura = _assinatura(estorno)
    alvo = next((n for n in candidatas if _assinatura(n) == assinatura), None)
    if alvo is None:
        citadas = ", ".join(n.n_nf for n in candidatas) or "nenhuma importada"
        PendenciaMatching.objects.create(
            motivo=PendenciaMatching.Motivo.ESTORNO_NAO_CONFERE,
            detalhe=(f"[estorno {estorno.pk}] NF {estorno.n_nf} (entrada própria / estorno) não anulou "
                     f"nada: notas citadas: {citadas}. Se a nota anulada ainda não foi importada, "
                     "isso se resolve sozinho quando ela for; senão, confira os itens das duas."))
        return None
    if alvo.situacao == NotaFiscal.Situacao.ANULADA and alvo.anulada_por_id == estorno.pk:
        return alvo
    saidas_soltas = desfazer_baixas_da_nota(alvo)
    NotaFiscal.objects.filter(pk=alvo.pk).update(
        situacao=NotaFiscal.Situacao.ANULADA, anulada_por=estorno, status=NotaFiscal.Status.OK)
    alvo.situacao = NotaFiscal.Situacao.ANULADA
    for saida in NotaFiscal.objects.filter(pk__in=saidas_soltas).order_by("data_emissao"):
        aplicar_baixas_da_nota(saida)
    return alvo


def aplicar_estornos_pendentes(nota: NotaFiscal) -> bool:
    """Chamado ao importar uma nota: se um estorno que cita ela já estava
    no banco (importado antes dela), aplica agora. True se ela foi anulada."""
    estornos = NotaFiscal.objects.filter(
        situacao=NotaFiscal.Situacao.ESTORNO, emit_cnpj=nota.emit_cnpj, notas_anuladas__isnull=True)
    for estorno in estornos:
        if nota in _notas_citadas_pelo_estorno(estorno):
            aplicar_estorno(estorno)
            nota.refresh_from_db(fields=["situacao", "anulada_por", "status"])
            if nota.situacao == NotaFiscal.Situacao.ANULADA:
                return True
    return False


_JANELA_DUPLICIDADE = timedelta(hours=48)


def duplicatas_de(nota: NotaFiscal) -> list[NotaFiscal]:
    """Outras saídas válidas do mesmo emitente pro mesmo cliente, emitidas
    até 48h antes/depois, com exatamente os mesmos itens e quantidades —
    padrão de nota emitida, cancelada na SEFAZ (evento que não vem no XML)
    e reemitida."""
    if nota.tipo != NotaFiscal.Tipo.SAIDA or nota.situacao != NotaFiscal.Situacao.VALIDA:
        return []
    assinatura = _assinatura(nota)
    vizinhas = (NotaFiscal.objects.filter(
        tipo=NotaFiscal.Tipo.SAIDA, situacao=NotaFiscal.Situacao.VALIDA, cliente=nota.cliente,
        emit_cnpj=nota.emit_cnpj,
        data_emissao__gte=nota.data_emissao - _JANELA_DUPLICIDADE,
        data_emissao__lte=nota.data_emissao + _JANELA_DUPLICIDADE,
    ).exclude(pk=nota.pk).prefetch_related("itens"))
    return [n for n in vizinhas if _assinatura(n) == assinatura]


def detectar_duplicidade(nota: NotaFiscal) -> int:
    """Abre POSSIVEL_DUPLICIDADE nos itens de tecido da nota MAIS RECENTE
    do par (a baixa continua aplicada — não dá pra saber qual das duas foi
    cancelada sem a SEFAZ). Ligada aos itens de entrada que ela baixou, pro
    item aparecer "Com divergência" no Histórico. Resolve marcando uma das
    duas como cancelada (marcar_cancelada) ou justificando."""
    anteriores = [n for n in duplicatas_de(nota)
                  if (n.data_emissao, n.pk) < (nota.data_emissao, nota.pk)]
    if not anteriores:
        return 0
    outras = ", ".join(f"NF {n.n_nf} ({n.data_emissao:%d/%m/%Y %H:%M})" for n in anteriores)
    criadas = 0
    itens = nota.itens.filter(tipo_retorno=NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO)
    for item in itens:
        if not eh_ncm_controlado(item.ncm):
            continue
        if item.pendencias.filter(motivo=PendenciaMatching.Motivo.POSSIVEL_DUPLICIDADE).exists():
            continue
        criar_pendencia(
            item, PendenciaMatching.Motivo.POSSIVEL_DUPLICIDADE,
            (f"Mesmos itens e quantidades da {outras} (emitida pouco antes, mesmo cliente). "
             "As duas estão baixando saldo — confira na SEFAZ se uma delas foi cancelada e "
             "marque-a como cancelada; se as duas valem, ignore com justificativa."),
            [v.entrada_item for v in item.vinculos_saida.select_related("entrada_item")])
        criadas += 1
    if criadas:
        NotaFiscal.objects.filter(pk=nota.pk).update(status=NotaFiscal.Status.PENDENTE)
    return criadas


def marcar_cancelada(nota: NotaFiscal, usuario=None) -> None:
    """Decisão humana (conferido na SEFAZ): a nota foi cancelada. Sai da
    conta do saldo como uma anulada, e as pendências de duplicidade que
    apontavam pra ela (na nota irmã) se resolvem."""
    with transaction.atomic():
        saidas_soltas = desfazer_baixas_da_nota(nota)
        NotaFiscal.objects.filter(pk=nota.pk).update(
            situacao=NotaFiscal.Situacao.CANCELADA, status=NotaFiscal.Status.OK)
        nota.situacao = NotaFiscal.Situacao.CANCELADA
        for p in PendenciaMatching.objects.filter(
                resolvido=False, motivo=PendenciaMatching.Motivo.POSSIVEL_DUPLICIDADE,
                detalhe__contains=f"NF {nota.n_nf} ("):
            p.resolvido = True
            p.resolvido_por = usuario
            p.resolvido_em = timezone.now()
            p.detalhe += f"\n\nResolução: NF {nota.n_nf} marcada como cancelada."
            p.save(update_fields=["resolvido", "resolvido_por", "resolvido_em", "detalhe"])
            atualizar_status(p.saida_item.nota_fiscal)
        for saida in NotaFiscal.objects.filter(pk__in=saidas_soltas).order_by("data_emissao"):
            aplicar_baixas_da_nota(saida)


_CRITERIO_MANUAL = "Resolução manual"


@dataclass
class ResumoRecalculo:
    vinculos_apagados: int
    pendencias_apagadas: int
    notas_reprocessadas: int
    vinculos_criados: int
    pendencias_criadas: int
    notas_anuladas: int
    possiveis_duplicidades: int


def recalcular_baixas() -> ResumoRecalculo:
    """Refaz do zero todo o casamento automático — usado quando a regra
    muda (ex.: NCM novo em FISCAL_NCMS_CONTROLADOS, ajuste na cascata) e as
    baixas antigas ficaram erradas. Tudo numa transação só.

    Ordem: (1) zera baixas automáticas e pendências não revisadas; (2) saldo
    de toda entrada VALIDA volta pro recebido (menos baixas manuais); (3)
    aplica os estornos (notas anuladas saem da conta); (4) baixa as saídas
    válidas em ordem de emissão, que é a ordem em que o saldo foi de fato
    consumido; (5) marca as possíveis duplicidades.

    Preserva decisão humana: Vinculo de "Resolução manual" (e a baixa dele
    no saldo), nota marcada como CANCELADA, itens com pendência ignorada
    com justificativa (sem baixa) e excessos de saldo já revisados (a baixa
    é refeita, mas não volta a abrir pendência)."""
    with transaction.atomic():
        revisadas = PendenciaMatching.objects.filter(resolvido=True, resolvido_por__isnull=False)
        itens_manuais = set(
            Vinculo.objects.filter(criterio=_CRITERIO_MANUAL).values_list("saida_item_id", flat=True))
        excesso_ja_revisado = frozenset(
            revisadas.filter(motivo=PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA)
            .values_list("saida_item_id", flat=True))
        duplicidade_revisada = frozenset(
            revisadas.filter(motivo=PendenciaMatching.Motivo.POSSIVEL_DUPLICIDADE)
            .values_list("saida_item__nota_fiscal_id", flat=True))
        ignorados = set(
            revisadas.exclude(motivo__in=[PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA,
                                          PendenciaMatching.Motivo.POSSIVEL_DUPLICIDADE])
            .values_list("saida_item_id", flat=True)) - itens_manuais
        pular = frozenset(itens_manuais | ignorados)

        vinculos_apagados, _ = Vinculo.objects.exclude(criterio=_CRITERIO_MANUAL).delete()
        pendencias_apagadas, _ = (
            PendenciaMatching.objects.filter(
                Q(saida_item__isnull=False) | Q(motivo=PendenciaMatching.Motivo.ESTORNO_NAO_CONFERE))
            .exclude(pk__in=revisadas.values("pk")).delete())
        NotaFiscal.objects.filter(situacao=NotaFiscal.Situacao.ANULADA).update(
            situacao=NotaFiscal.Situacao.VALIDA, anulada_por=None)

        # Saldo volta pro recebido menos só as baixas manuais que ficaram.
        manuais_por_item: dict[int, Decimal] = {}
        for entrada_id, qtd in Vinculo.objects.values_list("entrada_item_id", "quantidade_baixada"):
            manuais_por_item[entrada_id] = manuais_por_item.get(entrada_id, Decimal(0)) + qtd
        entradas = NotaFiscalItem.objects.filter(nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA)
        atualizar = []
        for item in entradas.select_related("nota_fiscal").only(
                "id", "ncm", "q_com", "saldo_atual", "nota_fiscal__situacao"):
            valida = item.nota_fiscal.situacao == NotaFiscal.Situacao.VALIDA
            if valida and (eh_ncm_controlado(item.ncm) or item.pk in manuais_por_item):
                novo = item.q_com - manuais_por_item.get(item.pk, Decimal(0))
            else:
                novo = None
            if novo != item.saldo_atual:
                item.saldo_atual = novo
                atualizar.append(item)
        NotaFiscalItem.objects.bulk_update(atualizar, ["saldo_atual"], batch_size=500)

        anuladas = 0
        for estorno in NotaFiscal.objects.filter(situacao=NotaFiscal.Situacao.ESTORNO).order_by("data_emissao"):
            if aplicar_estorno(estorno):
                anuladas += 1

        saidas = (NotaFiscal.objects.filter(tipo=NotaFiscal.Tipo.SAIDA)
                  .exclude(status=NotaFiscal.Status.ERRO)
                  .select_related("cliente").order_by("data_emissao", "n_nf"))
        saidas.update(status=NotaFiscal.Status.OK)
        pendencias_criadas = 0
        notas = 0
        for nota in saidas.filter(situacao=NotaFiscal.Situacao.VALIDA):
            pendencias_criadas += aplicar_baixas_da_nota(
                nota, pular_itens=pular, excesso_ja_revisado=excesso_ja_revisado)
            notas += 1
        duplicidades = 0
        for nota in saidas.filter(situacao=NotaFiscal.Situacao.VALIDA).exclude(pk__in=duplicidade_revisada):
            duplicidades += detectar_duplicidade(nota)

        return ResumoRecalculo(
            vinculos_apagados=vinculos_apagados, pendencias_apagadas=pendencias_apagadas,
            notas_reprocessadas=notas,
            vinculos_criados=Vinculo.objects.exclude(criterio=_CRITERIO_MANUAL).count(),
            pendencias_criadas=pendencias_criadas + duplicidades,
            notas_anuladas=anuladas, possiveis_duplicidades=duplicidades)


def reprocessar_pendencias_aguardando(entrada_importada: NotaFiscal) -> int:
    """Chamado logo depois de confirmar a importação de uma NF de ENTRADA —
    tenta resolver sozinhas as pendências `AGUARDANDO_ENTRADA` do mesmo
    cliente (a referência já tinha sido reconhecida, só faltava a nota
    existir). Porta o comportamento do protótipo original, onde a
    reconciliação inteira reroda a cada mudança; aqui, em vez de recalcular
    tudo, só revisita quem estava esperando exatamente por essa entrada.

    Se agora a entrada existe mas o casamento cai em outro problema
    (produto ambíguo, unidade...), a pendência troca de motivo — não fica
    "aguardando" algo que já chegou."""
    pendencias = PendenciaMatching.objects.filter(
        resolvido=False, motivo=PendenciaMatching.Motivo.AGUARDANDO_ENTRADA,
        saida_item__nota_fiscal__cliente=entrada_importada.cliente,
        saida_item__nota_fiscal__situacao=NotaFiscal.Situacao.VALIDA,
    ).select_related("saida_item", "saida_item__nota_fiscal")

    resolvidas = 0
    for pendencia in pendencias:
        item = pendencia.saida_item
        resultado = encontrar_entrada_candidata(
            entrada_importada.cliente, item,
            inf_cpl=item.nota_fiscal.inf_cpl, ref_nfe=item.nota_fiscal.ref_nfe_lista)
        if resultado.encontrado:
            aviso = aplicar_baixa(item, resultado.entrada_item, criterio=resultado.legenda)
            pendencia.resolvido = True
            pendencia.resolvido_em = timezone.now()
            pendencia.detalhe += f"\n\nResolvido automaticamente após importar a NF {entrada_importada.n_nf}."
            pendencia.save(update_fields=["resolvido", "resolvido_em", "detalhe"])
            if aviso:
                criar_pendencia(item, PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA, aviso,
                                [resultado.entrada_item])
            resolvidas += 1
        elif resultado.pendente and resultado.motivo_pendencia != PendenciaMatching.Motivo.AGUARDANDO_ENTRADA:
            pendencia.delete()
            criar_pendencia(item, resultado.motivo_pendencia, resultado.detalhe, resultado.itens_afetados)
        atualizar_status(item.nota_fiscal)
    return resolvidas
