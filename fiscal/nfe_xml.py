"""
Leitura do XML de NF-e (modelo 55) — só o que o Saldo Fiscal precisa: dados
de cabeçalho, itens e o `infAdProd` de cada item (referência à NF de entrada
que ele baixa, quando é uma devolução). `xml.etree.ElementTree` (stdlib) —
nenhum outro módulo da Central usa `lxml`, e o volume/tamanho dos arquivos
não justifica a dependência nativa extra.

Confirmado com 2 XMLs reais (uma entrada, uma saída de retorno de
industrialização): o namespace do NF-e é sempre
"http://www.portalfiscal.inf.br/nfe", tratado explicitamente em todo find.

Nunca decide sozinho quem é "cliente" nem se a nota é ENTRADA/SAÍDA — isso
depende do cadastro de Cliente (banco de dados), então fica em
fiscal/importador.py::identificar_nota. Este módulo só faz o parse puro do
XML pra uma estrutura Python, testável sem banco.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings

NFE_NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

# CFOPs de retorno que geram baixa automática — devolução de insumo/matéria-
# prima não utilizada na industrialização (5902/6902 = mesma operação,
# 5/6 só diferencia dentro/fora do estado).
CFOPS_DEVOLUCAO_INSUMO = {"5902", "6902"}
# CFOPs de entrega do produto já industrializado — não gera baixa de saldo,
# só é registrado como informação (o insumo virou produto, não "voltou").
CFOPS_ENTREGA_PRODUTO = {"5124", "6124"}


def eh_ncm_controlado(ncm: str) -> bool:
    """V1 só controla o tecido em si — insumos de produção (etiqueta,
    embalagem, etc.) não entram no saldo/casamento automático por enquanto
    (o controle desses vai ficar a cargo do almoxarife, mais pra frente).
    `settings.FISCAL_NCMS_CONTROLADOS` é a lista de NCMs tratados como
    "tecido"; ampliar essa lista é a forma de estender o controle depois."""
    return ncm in settings.FISCAL_NCMS_CONTROLADOS


class XmlInvalido(ValueError):
    """Erro de parse com mensagem já pronta pra mostrar na tela de
    importação — nunca deixa um traceback cru chegar no usuário."""


@dataclass
class ItemParseado:
    n_item: int
    c_prod: str
    x_prod: str
    ncm: str
    cfop: str
    u_com: str
    q_com: Decimal
    v_un_com: Decimal
    v_prod: Decimal
    inf_ad_prod: str

    @property
    def tipo_retorno(self) -> str:
        if self.cfop in CFOPS_DEVOLUCAO_INSUMO:
            return "DEVOLUCAO_INSUMO"
        if self.cfop in CFOPS_ENTREGA_PRODUTO:
            return "ENTREGA_PRODUTO"
        return "NA"


@dataclass
class NotaFiscalParseada:
    chave_acesso: str
    n_nf: str
    serie: str
    nat_op: str
    data_emissao: datetime
    emit_cnpj: str
    emit_nome: str
    emit_fantasia: str
    emit_municipio: str
    dest_cnpj: str
    dest_nome: str
    dest_fantasia: str
    dest_municipio: str
    valor_total: Decimal
    itens: list[ItemParseado]
    xml_bruto: str
    avisos: list[str] = field(default_factory=list)


def _texto(elemento: ET.Element | None, caminho: str) -> str:
    if elemento is None:
        return ""
    valor = elemento.findtext(caminho, namespaces=NFE_NS)
    return valor.strip() if valor else ""


def _decimal(valor: str, padrao: str = "0") -> Decimal:
    try:
        return Decimal(valor or padrao)
    except InvalidOperation:
        return Decimal(padrao)


def _parse_data(valor: str) -> datetime:
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        raise XmlInvalido(f'Data de emissão inválida no XML: "{valor}".') from None


def _parse_item(det: ET.Element) -> ItemParseado:
    prod = det.find("nfe:prod", NFE_NS)
    if prod is None:
        raise XmlInvalido(f'Item {det.get("nItem")} sem bloco <prod> — XML fora do padrão NF-e.')
    n_item_raw = det.get("nItem", "0")
    return ItemParseado(
        n_item=int(n_item_raw) if n_item_raw.isdigit() else 0,
        c_prod=_texto(prod, "nfe:cProd"),
        x_prod=_texto(prod, "nfe:xProd"),
        ncm=_texto(prod, "nfe:NCM"),
        cfop=_texto(prod, "nfe:CFOP"),
        u_com=_texto(prod, "nfe:uCom"),
        q_com=_decimal(_texto(prod, "nfe:qCom")),
        v_un_com=_decimal(_texto(prod, "nfe:vUnCom")),
        v_prod=_decimal(_texto(prod, "nfe:vProd")),
        inf_ad_prod=_texto(det, "nfe:infAdProd"),
    )


def parse_nfe(conteudo: bytes, nome_arquivo: str = "") -> NotaFiscalParseada:
    """Lê o XML bruto (bytes) e devolve os dados já estruturados. Levanta
    `XmlInvalido` (mensagem pronta pra tela) quando o arquivo não é um NF-e
    reconhecível — nunca deixa uma exceção genérica de XML vazar pra view."""
    try:
        root = ET.fromstring(conteudo)
    except ET.ParseError as e:
        raise XmlInvalido(f'"{nome_arquivo}" não é um XML válido: {e}') from None

    inf_nfe = root.find(".//nfe:infNFe", NFE_NS)
    if inf_nfe is None:
        raise XmlInvalido(f'"{nome_arquivo}" não tem a estrutura de uma NF-e (bloco <infNFe> não encontrado).')

    avisos: list[str] = []

    chave_protocolo = root.findtext(".//nfe:protNFe/nfe:infProt/nfe:chNFe", namespaces=NFE_NS)
    chave = chave_protocolo or (inf_nfe.get("Id", "") or "").replace("NFe", "")
    if len(chave) != 44:
        raise XmlInvalido(f'"{nome_arquivo}": chave de acesso inválida ("{chave}").')

    c_stat = root.findtext(".//nfe:protNFe/nfe:infProt/nfe:cStat", namespaces=NFE_NS)
    if c_stat is not None and c_stat != "100":
        motivo = root.findtext(".//nfe:protNFe/nfe:infProt/nfe:xMotivo", namespaces=NFE_NS, default="")
        avisos.append(f"NF sem autorização de uso confirmada (cStat={c_stat}: {motivo}).")

    ide = inf_nfe.find("nfe:ide", NFE_NS)
    emit = inf_nfe.find("nfe:emit", NFE_NS)
    dest = inf_nfe.find("nfe:dest", NFE_NS)

    itens = [_parse_item(det) for det in inf_nfe.findall("nfe:det", NFE_NS)]
    if not itens:
        avisos.append("NF sem nenhum item (<det>) reconhecido.")

    return NotaFiscalParseada(
        chave_acesso=chave,
        n_nf=_texto(ide, "nfe:nNF"),
        serie=_texto(ide, "nfe:serie"),
        nat_op=_texto(ide, "nfe:natOp"),
        data_emissao=_parse_data(_texto(ide, "nfe:dhEmi")),
        emit_cnpj=_texto(emit, "nfe:CNPJ"),
        emit_nome=_texto(emit, "nfe:xNome"),
        emit_fantasia=_texto(emit, "nfe:xFant"),
        emit_municipio=_texto(emit, "nfe:enderEmit/nfe:xMun"),
        dest_cnpj=_texto(dest, "nfe:CNPJ"),
        dest_nome=_texto(dest, "nfe:xNome"),
        dest_fantasia=_texto(dest, "nfe:xFant"),
        dest_municipio=_texto(dest, "nfe:enderDest/nfe:xMun"),
        valor_total=_decimal(root.findtext(".//nfe:total/nfe:ICMSTot/nfe:vNF", namespaces=NFE_NS)),
        itens=itens,
        xml_bruto=conteudo.decode("utf-8", errors="replace"),
        avisos=avisos,
    )
