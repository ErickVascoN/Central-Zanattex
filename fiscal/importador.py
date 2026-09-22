"""
Ponte entre o XML já parseado (fiscal/nfe_xml.py) e o banco: decide se a NF
é entrada ou saída da Zanattex, resolve o Cliente pelo CNPJ, e grava
NotaFiscal + itens. Usado tanto pelo upload web em 2 passos (views.py)
quanto pela carga em lote (management/commands/importar_entradas.py) —
única fonte da regra de identificação e persistência, pra nunca divergir
entre os dois caminhos.

Nunca cria Cliente sozinho: CNPJ sem cadastro correspondente pausa a
importação daquele arquivo (`identificar_nota` devolve `cliente=None`) em
vez de adivinhar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from . import matching
from .models import Cliente, NotaFiscal, NotaFiscalItem
from .nfe_xml import NotaFiscalParseada, XmlInvalido, eh_ncm_controlado, parse_nfe


@dataclass
class Identificacao:
    tipo: str
    cliente: Cliente | None
    cnpj_cliente: str
    nome_cliente: str
    centro_custo: str


def identificar_nota(parsed: NotaFiscalParseada) -> Identificacao:
    """Compara o CNPJ fixo da Zanattex (settings.FISCAL_CNPJ_ZANATTEX) com
    emit/dest do XML pra decidir ENTRADA/SAÍDA, e resolve o Cliente pelo
    CNPJ do outro lado. `centro_custo` vem do lado Zanattex da nota
    (xFant, com fallback pro município do XML não trouxer nome fantasia)."""
    zanattex = settings.FISCAL_CNPJ_ZANATTEX
    if parsed.emit_cnpj == zanattex:
        tipo = NotaFiscal.Tipo.SAIDA
        cnpj_cliente, nome_cliente = parsed.dest_cnpj, parsed.dest_nome
        centro_custo = parsed.emit_fantasia or parsed.emit_municipio
    elif parsed.dest_cnpj == zanattex:
        tipo = NotaFiscal.Tipo.ENTRADA
        cnpj_cliente, nome_cliente = parsed.emit_cnpj, parsed.emit_nome
        centro_custo = parsed.dest_fantasia or parsed.dest_municipio
    else:
        raise XmlInvalido(
            "Esta NF não tem a Zanattex nem como emitente nem como destinatário "
            f"(emitente {parsed.emit_cnpj}, destinatário {parsed.dest_cnpj}).")

    cliente = Cliente.objects.filter(cnpj=cnpj_cliente, ativo=True).first()
    return Identificacao(tipo, cliente, cnpj_cliente, nome_cliente, centro_custo)


@dataclass
class ItemPrevia:
    """Uma linha da tela de revisão — o que vai acontecer com cada item
    antes do usuário confirmar."""
    descricao: str
    quantidade: Decimal
    unidade: str
    cfop: str
    situacao: str
    detalhe: str = ""


@dataclass
class PreviaImportacao:
    parsed: NotaFiscalParseada
    identificacao: Identificacao
    ja_importada: bool
    itens: list[ItemPrevia] = field(default_factory=list)

    @property
    def pode_confirmar(self) -> bool:
        return not self.ja_importada and self.identificacao.cliente is not None


def montar_previa(conteudo: bytes, nome_arquivo: str) -> PreviaImportacao:
    """Parse + identificação + simulação do casamento automático, sem
    gravar nada — é o que a tela de revisão do upload mostra antes de
    confirmar (inclusive quais itens de devolução vão ficar pendentes)."""
    parsed = parse_nfe(conteudo, nome_arquivo)
    identificacao = identificar_nota(parsed)
    ja_importada = NotaFiscal.objects.filter(chave_acesso=parsed.chave_acesso).exists()

    itens_previa = []
    if identificacao.cliente is not None and not ja_importada:
        for item in parsed.itens:
            if not eh_ncm_controlado(item.ncm):
                situacao, detalhe = "Fora do escopo", "Insumo de produção — controle não implementado ainda."
            elif item.tipo_retorno == "DEVOLUCAO_INSUMO":
                resultado = matching.encontrar_entrada_candidata(identificacao.cliente, item)
                if resultado.encontrado:
                    situacao = "Baixa automática"
                    detalhe = (
                        f"NF {resultado.entrada_item.nota_fiscal.n_nf}, "
                        f"item #{resultado.entrada_item.n_item}")
                else:
                    situacao, detalhe = "Pendência", resultado.detalhe
            elif item.tipo_retorno == "ENTREGA_PRODUTO":
                situacao, detalhe = "Produto entregue", "Informativo, sem baixa de saldo."
            else:
                situacao, detalhe = "Entrada de tecido", ""
            itens_previa.append(ItemPrevia(
                descricao=item.x_prod, quantidade=item.q_com, unidade=item.u_com,
                cfop=item.cfop, situacao=situacao, detalhe=detalhe))

    return PreviaImportacao(parsed, identificacao, ja_importada, itens_previa)


@dataclass
class ResultadoConfirmacao:
    """status: "importada" | "duplicada" | "cliente_pendente" — usado pela
    tela de confirmação (e pelo comando de carga em lote) pra somar um
    resumo sem confundir "já importada antes" com "cliente desconhecido",
    que exigem ações bem diferentes do usuário."""
    status: str
    nota: NotaFiscal | None = None
    cnpj_cliente: str = ""
    nome_cliente: str = ""


def confirmar_importacao(conteudo: bytes, nome_arquivo: str, usuario=None) -> ResultadoConfirmacao:
    """Reprocessa o XML e grava de verdade: NotaFiscal + itens (bulk_create,
    saldo inicial nas entradas), casamento automático nas saídas. Não grava
    nada quando a NF já foi importada (idempotente por chave_acesso) ou
    quando o cliente ainda não está cadastrado — os dois casos voltam sem
    erro, só com o status correspondente."""
    parsed = parse_nfe(conteudo, nome_arquivo)
    identificacao = identificar_nota(parsed)

    if identificacao.cliente is None:
        return ResultadoConfirmacao(
            "cliente_pendente", cnpj_cliente=identificacao.cnpj_cliente,
            nome_cliente=identificacao.nome_cliente)
    if NotaFiscal.objects.filter(chave_acesso=parsed.chave_acesso).exists():
        return ResultadoConfirmacao("duplicada")

    eh_entrada = identificacao.tipo == NotaFiscal.Tipo.ENTRADA
    with transaction.atomic():
        nota = NotaFiscal.objects.create(
            cliente=identificacao.cliente, tipo=identificacao.tipo,
            chave_acesso=parsed.chave_acesso, n_nf=parsed.n_nf, serie=parsed.serie,
            nat_op=parsed.nat_op, data_emissao=parsed.data_emissao,
            emit_cnpj=parsed.emit_cnpj, emit_nome=parsed.emit_nome,
            dest_cnpj=parsed.dest_cnpj, dest_nome=parsed.dest_nome,
            centro_custo=identificacao.centro_custo, valor_total=parsed.valor_total,
            arquivo_origem=nome_arquivo, importado_por=usuario, xml_bruto=parsed.xml_bruto,
        )
        NotaFiscalItem.objects.bulk_create([
            NotaFiscalItem(
                nota_fiscal=nota, n_item=item.n_item, c_prod=item.c_prod, x_prod=item.x_prod,
                ncm=item.ncm, cfop=item.cfop, u_com=item.u_com, q_com=item.q_com,
                v_un_com=item.v_un_com, v_prod=item.v_prod, inf_ad_prod=item.inf_ad_prod,
                tipo_retorno=item.tipo_retorno,
                # Saldo só é iniciado pra itens de entrada dentro do escopo
                # controlado (tecido) — insumo de produção fica de fora do
                # controle de saldo por enquanto (ver eh_ncm_controlado).
                saldo_atual=item.q_com if eh_entrada and eh_ncm_controlado(item.ncm) else None,
            )
            for item in parsed.itens
        ])
        if not eh_entrada:
            matching.aplicar_baixas_da_nota(nota)

    return ResultadoConfirmacao("importada", nota=nota)
