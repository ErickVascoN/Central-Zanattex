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

from dataclasses import dataclass
from typing import Protocol

from django.db import transaction
from django.db.models import F

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

    @property
    def encontrado(self) -> bool:
        return self.entrada_item is not None

    @property
    def pendente(self) -> bool:
        return self.motivo_pendencia is not None


def _localizar_entrada_por_numero(cliente, numero: str) -> NotaFiscal | None:
    nota = NotaFiscal.objects.filter(
        cliente=cliente, tipo=NotaFiscal.Tipo.ENTRADA, n_nf=numero).first()
    if nota:
        return nota
    # Fallback: número pode vir com formatação de zeros à esquerda diferente
    # da fonte original — compara os dois lados sem zeros à esquerda. Volume
    # de NFs de entrada por cliente é pequeno, então varrer é barato.
    numero_normalizado = numero.lstrip("0") or "0"
    for candidata in NotaFiscal.objects.filter(cliente=cliente, tipo=NotaFiscal.Tipo.ENTRADA):
        if (candidata.n_nf.lstrip("0") or "0") == numero_normalizado:
            return candidata
    return None


def _localizar_entrada_por_chave(cliente, chave: str) -> NotaFiscal | None:
    return NotaFiscal.objects.filter(
        cliente=cliente, tipo=NotaFiscal.Tipo.ENTRADA, chave_acesso=chave).first()


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
    """Um nível da cascata: devolve (item, legenda, None) se sobrou
    exatamente 1, (None, None, mensagem) se ficou ambíguo (para ali, não
    tenta os próximos níveis), ou (None, None, None) se ficou vazio (tenta
    o próximo nível)."""
    if len(candidatos) == 1:
        return candidatos[0], legenda, None
    if len(candidatos) > 1:
        opcoes = "; ".join(f"item #{c.n_item} {c.x_prod} (saldo {c.saldo_atual})" for c in candidatos)
        return None, None, f"NF {entrada.n_nf}: mais de um item candidato por {legenda} — {opcoes}."
    return None, None, None


def _resolver_item_entrada(entrada: NotaFiscal, item: ItemComReferencia, cliente):
    """Cascata de casamento dentro da NF de entrada já resolvida — NCM
    primeiro (nosso critério principal), depois a cascata do protótipo
    original: código do produto, associação aprendida, código citado em
    texto livre, único item com saldo na nota, descrição idêntica,
    descrição compatível (contida numa na outra)."""
    base = list(entrada.itens.filter(saldo_atual__gt=0))

    resolvido, legenda, erro = _nivel([i for i in base if i.ncm == item.ncm], entrada, "NCM")
    if resolvido or erro:
        return resolvido, legenda, erro

    resolvido, legenda, erro = _nivel([i for i in base if i.c_prod == item.c_prod], entrada, "código do produto")
    if resolvido or erro:
        return resolvido, legenda, erro

    associacao = AssociacaoProduto.objects.filter(cliente=cliente, cprod_saida=item.c_prod).first()
    if associacao:
        resolvido, legenda, erro = _nivel(
            [i for i in base if i.c_prod == associacao.cprod_entrada], entrada, "associação aprendida")
        if resolvido or erro:
            return resolvido, legenda, erro

    if item.inf_ad_prod:
        resolvido, legenda, erro = _nivel(
            [i for i in base if i.c_prod and i.c_prod in item.inf_ad_prod],
            entrada, "código citado no infAdProd")
        if resolvido or erro:
            return resolvido, legenda, erro

    if len(base) == 1:
        return base[0], "único item com saldo na NF", None

    resolvido, legenda, erro = _nivel(
        [i for i in base if referencia.descricoes_identicas(item.x_prod, i.x_prod)],
        entrada, "descrição idêntica")
    if resolvido or erro:
        return resolvido, legenda, erro

    resolvido, legenda, erro = _nivel(
        [i for i in base if referencia.descricao_contida(item.x_prod, i.x_prod)
         or referencia.descricao_contida(i.x_prod, item.x_prod)],
        entrada, "descrição compatível")
    return resolvido, legenda, erro


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

    item_resolvido, legenda, erro_ambiguo = _resolver_item_entrada(entrada, item, cliente)
    if erro_ambiguo:
        return ResultadoCasamento(None, PendenciaMatching.Motivo.MULTIPLOS_PRODUTOS, erro_ambiguo)
    if item_resolvido is None:
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.PRODUTO_SEM_CORRESPONDENTE,
            f'NF {entrada.n_nf}: nenhum item com saldo disponível corresponde '
            f'(NCM "{item.ncm}", código "{item.c_prod}").')

    if not referencia.unidades_compativeis(item.u_com, item_resolvido.u_com):
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.UNIDADE_INCOMPATIVEL,
            f'NF {entrada.n_nf} item #{item_resolvido.n_item}: saída em "{item.u_com}", entrada em '
            f'"{item_resolvido.u_com}" — confirme manualmente antes de baixar.')

    return ResultadoCasamento(item_resolvido, None, legenda=legenda)


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


def aplicar_baixas_da_nota(nota_saida: NotaFiscal) -> int:
    """Roda o casamento pra todos os itens de devolução/perda de uma NF de
    saída já persistida (itens já gravados no banco). Chamado logo após o
    `bulk_create` dos itens, tanto no upload web quanto na carga em lote.
    Devolve a quantidade de pendências criadas; marca a NF como PENDENTE
    quando há pelo menos uma.

    Itens de NCM fora de `FISCAL_NCMS_CONTROLADOS` (insumo de produção, não
    tecido) nem entram no casamento — v1 não controla esses, não é
    "pendência", é fora de escopo (ver eh_ncm_controlado)."""
    pendencias_criadas = 0
    itens = nota_saida.itens.filter(tipo_retorno=NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO)
    for item in itens:
        if not eh_ncm_controlado(item.ncm):
            continue
        resultado = encontrar_entrada_candidata(
            nota_saida.cliente, item, inf_cpl=nota_saida.inf_cpl, ref_nfe=nota_saida.ref_nfe_lista)
        if resultado.encontrado:
            aviso = aplicar_baixa(item, resultado.entrada_item, criterio=resultado.legenda)
            if aviso:
                PendenciaMatching.objects.create(
                    saida_item=item, motivo=PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA, detalhe=aviso)
                pendencias_criadas += 1
        elif resultado.pendente:
            PendenciaMatching.objects.create(
                saida_item=item, motivo=resultado.motivo_pendencia, detalhe=resultado.detalhe)
            pendencias_criadas += 1

    if pendencias_criadas:
        NotaFiscal.objects.filter(pk=nota_saida.pk).update(status=NotaFiscal.Status.PENDENTE)
    return pendencias_criadas


def reprocessar_pendencias_aguardando(entrada_importada: NotaFiscal) -> int:
    """Chamado logo depois de confirmar a importação de uma NF de ENTRADA —
    tenta resolver sozinhas as pendências `AGUARDANDO_ENTRADA` do mesmo
    cliente (a referência já tinha sido reconhecida, só faltava a nota
    existir). Porta o comportamento do protótipo original, onde a
    reconciliação inteira reroda a cada mudança; aqui, em vez de recalcular
    tudo, só revisita quem estava esperando exatamente por essa entrada."""
    pendencias = PendenciaMatching.objects.filter(
        resolvido=False, motivo=PendenciaMatching.Motivo.AGUARDANDO_ENTRADA,
        saida_item__nota_fiscal__cliente=entrada_importada.cliente,
    ).select_related("saida_item", "saida_item__nota_fiscal")

    resolvidas = 0
    for pendencia in pendencias:
        item = pendencia.saida_item
        resultado = encontrar_entrada_candidata(
            entrada_importada.cliente, item,
            inf_cpl=item.nota_fiscal.inf_cpl, ref_nfe=item.nota_fiscal.ref_nfe_lista)
        if not resultado.encontrado:
            continue
        aviso = aplicar_baixa(item, resultado.entrada_item, criterio=resultado.legenda)
        pendencia.resolvido = True
        pendencia.resolvido_em = None
        pendencia.detalhe += f"\n\nResolvido automaticamente após importar a NF {entrada_importada.n_nf}."
        pendencia.save(update_fields=["resolvido", "detalhe"])
        if aviso:
            PendenciaMatching.objects.create(
                saida_item=item, motivo=PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA, detalhe=aviso)
        resolvidas += 1
    return resolvidas
