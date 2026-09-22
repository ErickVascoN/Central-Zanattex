"""
O "consumo automático": casa um item de devolução (NF de saída, CFOP de
insumo) com o item da NF de entrada de onde ele está sendo baixado.

Regra confirmada com XMLs reais (ver plano do módulo):
1. `infAdProd` do item de saída = número (`n_nf`) da NF de entrada.
2. Dentro dessa entrada, o NCM do item de saída identifica QUAL item baixar
   — é o campo padronizado pelo governo, mais confiável que `cProd` (código
   interno, pode divergir entre o que o cliente usa e o que a Zanattex usa).
3. Zero ou mais de um candidato nunca "chuta": vira PendenciaMatching pra
   revisão humana. Fiscal é o tipo de dado que não pode errar em silêncio.

`encontrar_entrada_candidata` aceita tanto um `ItemParseado` (fiscal/nfe_xml.py,
usado na prévia antes de gravar nada) quanto um `NotaFiscalItem` já salvo
(usado na aplicação de verdade) — os dois expõem os mesmos 4 atributos
(`ncm`, `c_prod`, `inf_ad_prod`, `tipo_retorno`), então a função não precisa
saber qual é qual.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.db import transaction
from django.db.models import F

from .models import NotaFiscal, NotaFiscalItem, PendenciaMatching, Vinculo
from .nfe_xml import eh_ncm_controlado


class ItemComReferencia(Protocol):
    ncm: str
    c_prod: str
    inf_ad_prod: str
    tipo_retorno: str


@dataclass
class ResultadoCasamento:
    entrada_item: NotaFiscalItem | None
    motivo_pendencia: str | None
    detalhe: str = ""

    @property
    def encontrado(self) -> bool:
        return self.entrada_item is not None

    @property
    def pendente(self) -> bool:
        return self.motivo_pendencia is not None


def encontrar_entrada_candidata(cliente, item: ItemComReferencia) -> ResultadoCasamento:
    """Só leitura — não grava nada. Usado na prévia da importação (pra
    mostrar o que vai casar antes do usuário confirmar) e na aplicação real
    (ver `aplicar_baixas_da_nota`)."""
    if item.tipo_retorno != NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO:
        return ResultadoCasamento(None, None)  # produto acabado ou item de entrada: não se aplica

    if not item.inf_ad_prod:
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.NF_NAO_ENCONTRADA,
            "Item de devolução sem infAdProd preenchido no XML.")

    entrada = NotaFiscal.objects.filter(
        cliente=cliente, tipo=NotaFiscal.Tipo.ENTRADA, n_nf=item.inf_ad_prod,
    ).first()
    if entrada is None:
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.NF_NAO_ENCONTRADA,
            f'Nenhuma NF de entrada com número "{item.inf_ad_prod}" para este cliente.')

    candidatos = list(entrada.itens.filter(ncm=item.ncm, saldo_atual__gt=0))
    if not candidatos:
        candidatos = list(entrada.itens.filter(c_prod=item.c_prod, saldo_atual__gt=0))
    if not candidatos:
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.ITEM_NAO_ENCONTRADO,
            f'NF {entrada.n_nf}: nenhum item com saldo disponível para NCM "{item.ncm}" '
            f'nem código "{item.c_prod}".')
    if len(candidatos) > 1:
        opcoes = "; ".join(f"item #{c.n_item} {c.x_prod} (saldo {c.saldo_atual})" for c in candidatos)
        return ResultadoCasamento(
            None, PendenciaMatching.Motivo.ITEM_AMBIGUO,
            f"NF {entrada.n_nf}: mais de um item candidato — {opcoes}.")

    return ResultadoCasamento(candidatos[0], None)


def aplicar_baixa(saida_item: NotaFiscalItem, entrada_item: NotaFiscalItem) -> str | None:
    """Cria o Vinculo e decrementa `saldo_atual` do item de entrada de forma
    atômica. Devolve um aviso quando a quantidade devolvida é maior que o
    saldo disponível — a baixa ainda é aplicada (o retorno físico
    aconteceu), só fica sinalizada pra revisão: saldo negativo é sempre
    sinal de um problema real (NF errada, entrada faltando)."""
    aviso = None
    if saida_item.q_com > entrada_item.saldo_atual:
        aviso = (
            f"Devolvido {saida_item.q_com} {saida_item.u_com}, saldo disponível era só "
            f"{entrada_item.saldo_atual} na NF {entrada_item.nota_fiscal.n_nf}.")
    with transaction.atomic():
        Vinculo.objects.create(
            saida_item=saida_item, entrada_item=entrada_item, quantidade_baixada=saida_item.q_com)
        NotaFiscalItem.objects.filter(pk=entrada_item.pk).update(
            saldo_atual=F("saldo_atual") - saida_item.q_com)
    return aviso


def aplicar_baixas_da_nota(nota_saida: NotaFiscal) -> int:
    """Roda o casamento pra todos os itens de devolução de uma NF de saída
    já persistida (itens já gravados no banco). Chamado logo após o
    `bulk_create` dos itens, tanto no upload web quanto na carga em lote.
    Devolve a quantidade de pendências criadas; marca a NF como PENDENTE
    quando há pelo menos uma.

    Itens de NCM fora de `FISCAL_NCMS_CONTROLADOS` (insumo de produção,
    não tecido) nem entram no casamento — v1 não controla esses, não é
    "pendência", é fora de escopo (ver eh_ncm_controlado)."""
    pendencias_criadas = 0
    itens = nota_saida.itens.filter(tipo_retorno=NotaFiscalItem.TipoRetorno.DEVOLUCAO_INSUMO)
    for item in itens:
        if not eh_ncm_controlado(item.ncm):
            continue
        resultado = encontrar_entrada_candidata(nota_saida.cliente, item)
        if resultado.encontrado:
            aviso = aplicar_baixa(item, resultado.entrada_item)
            if aviso:
                PendenciaMatching.objects.create(
                    saida_item=item, motivo=PendenciaMatching.Motivo.SALDO_INSUFICIENTE, detalhe=aviso)
                pendencias_criadas += 1
        elif resultado.pendente:
            PendenciaMatching.objects.create(
                saida_item=item, motivo=resultado.motivo_pendencia, detalhe=resultado.detalhe)
            pendencias_criadas += 1

    if pendencias_criadas:
        NotaFiscal.objects.filter(pk=nota_saida.pk).update(status=NotaFiscal.Status.PENDENTE)
    return pendencias_criadas
