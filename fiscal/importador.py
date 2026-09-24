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
from django.utils import timezone

from . import matching, referencia, sefaz
from .models import Cliente, NotaFiscal, NotaFiscalItem
from .nfe_xml import NotaFiscalParseada, XmlInvalido, eh_ncm_controlado, parse_nfe


@dataclass
class Identificacao:
    tipo: str
    cliente: Cliente | None
    cnpj_cliente: str
    nome_cliente: str
    centro_custo: str


def identificar_nota(
    parsed: NotaFiscalParseada, *, clientes_cache: dict[str, Cliente] | None = None,
) -> Identificacao:
    """Compara os CNPJs da Zanattex (settings.FISCAL_CNPJS_ZANATTEX — mais
    de uma unidade/razão social conta como "nós", ex.: Mega Preven) com
    emit/dest do XML pra decidir ENTRADA/SAÍDA, e resolve o Cliente pelo
    CNPJ do outro lado. `centro_custo` é o CNPJ do lado Zanattex da nota
    (emit na saída, dest na entrada) — cada CNPJ é um centro de custo; o
    rótulo legível vem de servicos.rotulos_centro_custo.

    `clientes_cache` (opcional) evita 1 query por nota quando quem chama já
    pré-carregou os clientes do lote inteiro (ver prefetch_clientes, usado
    pelo upload web com muitos XMLs de uma vez) — sem ele, comportamento
    de sempre (1 query por chamada)."""
    zanattex = settings.FISCAL_CNPJS_ZANATTEX
    if parsed.emit_cnpj in zanattex:
        tipo = NotaFiscal.Tipo.SAIDA
        cnpj_cliente, nome_cliente = parsed.dest_cnpj, parsed.dest_nome
        centro_custo = parsed.emit_cnpj
    elif parsed.dest_cnpj in zanattex:
        tipo = NotaFiscal.Tipo.ENTRADA
        cnpj_cliente, nome_cliente = parsed.emit_cnpj, parsed.emit_nome
        centro_custo = parsed.dest_cnpj
    else:
        raise XmlInvalido(
            "Esta NF não tem a Zanattex nem como emitente nem como destinatário "
            f"(emitente {parsed.emit_cnpj}, destinatário {parsed.dest_cnpj}).")

    if clientes_cache is not None:
        cliente = clientes_cache.get(cnpj_cliente)
    else:
        cliente = Cliente.objects.filter(cnpj=cnpj_cliente, ativo=True).first()
    return Identificacao(tipo, cliente, cnpj_cliente, nome_cliente, centro_custo)


def prefetch_clientes(lote: list[NotaFiscalParseada]) -> dict[str, Cliente]:
    """1 query pro lote inteiro em vez de 1 por nota — usada pelo upload web
    (fiscal/views.py) quando processa muitas notas de uma vez. Devolve um
    dict cnpj -> Cliente pra passar como `clientes_cache` adiante."""
    cnpjs = {p.emit_cnpj for p in lote} | {p.dest_cnpj for p in lote}
    return {c.cnpj: c for c in Cliente.objects.filter(cnpj__in=cnpjs, ativo=True)}


def prefetch_chaves_importadas(lote: list[NotaFiscalParseada]) -> set[str]:
    """1 query pro lote inteiro pra saber quais chaves já existem no banco —
    mesma ideia de prefetch_clientes. O set devolvido é seguro pra passar
    adiante pra confirmar_importacao_parsed: cada confirmação bem-sucedida
    adiciona a própria chave nele (mutação in-place), então uma duplicata
    DENTRO do mesmo lote (duas notas com a mesma chave no mesmo upload)
    continua sendo pega mesmo sem voltar ao banco."""
    chaves = {p.chave_acesso for p in lote}
    return set(NotaFiscal.objects.filter(chave_acesso__in=chaves).values_list("chave_acesso", flat=True))


def prefetch_situacoes_sefaz(
    lote: list[NotaFiscalParseada], *,
    clientes_cache: dict[str, Cliente] | None = None,
    chaves_importadas: set[str] | None = None,
) -> dict[str, sefaz.ResultadoConsultaSefaz]:
    """1 chamada em lote (paralela — ver fiscal/sefaz.py::consultar_situacao_lote)
    pro conjunto inteiro em vez de 1 por nota — é o que viabiliza checar o
    SEFAZ no ato do import sem estourar o corte de 60s do proxy do Fly (ver
    memory/fly-proxy-60s-lotes.md). Só consulta o que realmente seria
    gravado (cliente conhecido, ainda não importada) — nota que cairia fora
    por outro motivo não gasta chamada à SEFAZ."""
    pares = []
    for parsed in lote:
        identificacao = identificar_nota(parsed, clientes_cache=clientes_cache)
        ja_existe = (
            parsed.chave_acesso in chaves_importadas if chaves_importadas is not None
            else NotaFiscal.objects.filter(chave_acesso=parsed.chave_acesso).exists())
        if identificacao.cliente is not None and not ja_existe:
            pares.append((parsed.chave_acesso, identificacao.centro_custo))
    return sefaz.consultar_situacao_lote(pares)


def situacao_da_nota(
    parsed: NotaFiscalParseada, resultado_sefaz: sefaz.ResultadoConsultaSefaz | None = None,
) -> str:
    """Nota sem autorização de uso não vale; tpNF=0 (entrada emitida pelo
    próprio emitente) é estorno/anulação de outra nota — ver
    matching.aplicar_estorno. O resto é VALIDA.

    `resultado_sefaz` (ver fiscal/sefaz.py) é o critério mais forte de
    todos: se a SEFAZ confirma que a nota está cancelada, isso prevalece
    sobre autorizada/tp_nf — checado primeiro. `None`/NAO_VERIFICADA nunca
    marca cancelamento (falha de consulta não é fato)."""
    if resultado_sefaz is not None and resultado_sefaz.cancelada:
        return NotaFiscal.Situacao.CANCELADA
    if not parsed.autorizada:
        return NotaFiscal.Situacao.NAO_AUTORIZADA
    if parsed.tp_nf == "0":
        return NotaFiscal.Situacao.ESTORNO
    return NotaFiscal.Situacao.VALIDA


def _campos_verificacao_sefaz(resultado_sefaz: sefaz.ResultadoConsultaSefaz | None, situacao: str) -> dict:
    """Campos extras a gravar em NotaFiscal a partir do resultado da consulta
    (ver seção 3/4 do plano) — separado de situacao_da_nota porque grava
    mais que só a situação (protocolo, motivo, quando verificou)."""
    if resultado_sefaz is None:
        return {}
    campos: dict = {}
    if resultado_sefaz.situacao != sefaz.Situacao.NAO_VERIFICADA:
        campos["situacao_sefaz_verificada_em"] = timezone.now()
    if situacao == NotaFiscal.Situacao.CANCELADA and resultado_sefaz.cancelada:
        # "Nasce cancelada": nunca teve saldo/carteira de verdade, resolvido
        # sozinho — sem entrar na fila de revisão (ver fiscal/sefaz_servico.py,
        # Fase 2, pro caso inverso: nota que só é cancelada DEPOIS de já
        # estar valendo).
        campos.update(
            cancelamento_origem=NotaFiscal.CancelamentoOrigem.IMPORTACAO,
            cancelamento_detectado_em=timezone.now(),
            protocolo_cancelamento=resultado_sefaz.protocolo,
            motivo_cancelamento=resultado_sefaz.xmotivo,
        )
    return campos


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
    # Resultado bruto da consulta ao SEFAZ pra essa nota (ver fiscal/sefaz.py)
    # — None quando não foi consultada (já duplicada, cliente desconhecido).
    # A tela de revisão usa isso pra mostrar um selo (autorizada/cancelada/
    # não verificada) independente do que `itens` já explica por item.
    situacao_sefaz: sefaz.ResultadoConsultaSefaz | None = None

    @property
    def pode_confirmar(self) -> bool:
        return not self.ja_importada and self.identificacao.cliente is not None


def montar_previa(
    conteudo: bytes, nome_arquivo: str, *,
    clientes_cache: dict[str, Cliente] | None = None,
    chaves_importadas: set[str] | None = None,
    situacoes_sefaz: dict[str, sefaz.ResultadoConsultaSefaz] | None = None,
) -> PreviaImportacao:
    """Parse + identificação + simulação do casamento automático, sem
    gravar nada — é o que a tela de revisão do upload mostra antes de
    confirmar (inclusive quais itens de devolução vão ficar pendentes)."""
    parsed = parse_nfe(conteudo, nome_arquivo)
    return montar_previa_parsed(
        parsed, nome_arquivo, clientes_cache=clientes_cache, chaves_importadas=chaves_importadas,
        situacoes_sefaz=situacoes_sefaz)


def montar_previa_parsed(
    parsed: NotaFiscalParseada, nome_arquivo: str, *,
    clientes_cache: dict[str, Cliente] | None = None,
    chaves_importadas: set[str] | None = None,
    situacoes_sefaz: dict[str, sefaz.ResultadoConsultaSefaz] | None = None,
) -> PreviaImportacao:
    """Mesma coisa que montar_previa, a partir de um XML que quem chama já
    parseou antes (evita reparsear o mesmo arquivo duas vezes quando o
    lote inteiro é processado de uma vez — ver prefetch_clientes/
    prefetch_chaves_importadas e fiscal/views.py::_etapa1_upload).

    `situacoes_sefaz`, quando vem de fiscal/importador.py::prefetch_situacoes_sefaz,
    evita bater na SEFAZ nota a nota; sem ele (chamada avulsa), consulta na
    hora, uma chave só."""
    identificacao = identificar_nota(parsed, clientes_cache=clientes_cache)
    ja_importada = (
        parsed.chave_acesso in chaves_importadas if chaves_importadas is not None
        else NotaFiscal.objects.filter(chave_acesso=parsed.chave_acesso).exists()
    )

    resultado_sefaz = None
    if identificacao.cliente is not None and not ja_importada:
        resultado_sefaz = (
            situacoes_sefaz.get(parsed.chave_acesso) if situacoes_sefaz is not None
            else sefaz.consultar_situacao(parsed.chave_acesso, identificacao.centro_custo)
        )

    itens_previa = []
    situacao = situacao_da_nota(parsed, resultado_sefaz)
    if identificacao.cliente is not None and not ja_importada and situacao != NotaFiscal.Situacao.VALIDA:
        explicacao = {
            NotaFiscal.Situacao.NAO_AUTORIZADA: ("Sem autorização", "XML sem protocolo de autorização "
                                                 "(ou cStat recusado) — gravada, mas não mexe no saldo."),
            NotaFiscal.Situacao.ESTORNO: ("Estorno", "NF de entrada própria (tpNF=0): anula a nota citada "
                                          "no infCpl e desfaz as baixas dela."),
            NotaFiscal.Situacao.CANCELADA: ("Cancelada", (
                f"SEFAZ reporta esta NF como cancelada (protocolo {resultado_sefaz.protocolo or '—'}, "
                f"{resultado_sefaz.xmotivo or 'motivo não informado'}) — gravada, mas não mexe no saldo."
            )),
        }[situacao]
        itens_previa = [ItemPrevia(descricao=i.x_prod, quantidade=i.q_com, unidade=i.u_com, cfop=i.cfop,
                                   situacao=explicacao[0], detalhe=explicacao[1]) for i in parsed.itens]
    elif identificacao.cliente is not None and not ja_importada:
        for item in parsed.itens:
            if not eh_ncm_controlado(item.ncm):
                situacao, detalhe = "Fora do escopo", "Insumo de produção — controle não implementado ainda."
                if item.tipo_retorno == "DEVOLUCAO_INSUMO" and referencia.parece_tecido(item.x_prod):
                    # Reforço por palavra-chave (o NCM continua sendo o
                    # critério principal) — a descrição parece tecido mas o
                    # NCM não está na lista controlada, vale a pena
                    # confirmar o cadastro antes de descartar o item.
                    detalhe = (
                        f'NCM "{item.ncm}" não está na lista de tecido controlado, mas a descrição '
                        "parece ser tecido — confira o NCM antes de ignorar esta baixa.")
            elif item.tipo_retorno == "DEVOLUCAO_INSUMO":
                resultado = matching.encontrar_entrada_candidata(
                    identificacao.cliente, item, inf_cpl=parsed.inf_cpl, ref_nfe=parsed.ref_nfe)
                if resultado.encontrado:
                    situacao = "Baixa automática"
                    entrada_item = resultado.entrada_item
                    detalhe = (
                        f"Baixa na NF {entrada_item.nota_fiscal.n_nf} "
                        f"({entrada_item.nota_fiscal.centro_custo_rotulo}), item #{entrada_item.n_item} "
                        f"(casado por {resultado.legenda}) — saldo em aberto antes da baixa: "
                        f"{entrada_item.saldo_atual} {entrada_item.u_com}")
                else:
                    situacao, detalhe = "Pendência", resultado.detalhe
            elif item.tipo_retorno == "ENTREGA_PRODUTO":
                situacao, detalhe = "Produto entregue", "Informativo, sem baixa de saldo."
            else:
                situacao, detalhe = "Entrada de tecido", ""
            itens_previa.append(ItemPrevia(
                descricao=item.x_prod, quantidade=item.q_com, unidade=item.u_com,
                cfop=item.cfop, situacao=situacao, detalhe=detalhe))

    return PreviaImportacao(parsed, identificacao, ja_importada, itens_previa, situacao_sefaz=resultado_sefaz)


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


def confirmar_importacao(
    conteudo: bytes, nome_arquivo: str, usuario=None, *,
    clientes_cache: dict[str, Cliente] | None = None,
    chaves_importadas: set[str] | None = None,
    situacoes_sefaz: dict[str, sefaz.ResultadoConsultaSefaz] | None = None,
) -> ResultadoConfirmacao:
    """Reprocessa o XML e grava de verdade: NotaFiscal + itens (bulk_create,
    saldo inicial nas entradas), casamento automático nas saídas. Não grava
    nada quando a NF já foi importada (idempotente por chave_acesso) ou
    quando o cliente ainda não está cadastrado — os dois casos voltam sem
    erro, só com o status correspondente."""
    parsed = parse_nfe(conteudo, nome_arquivo)
    return confirmar_importacao_parsed(
        parsed, nome_arquivo, usuario=usuario,
        clientes_cache=clientes_cache, chaves_importadas=chaves_importadas,
        situacoes_sefaz=situacoes_sefaz)


def confirmar_importacao_parsed(
    parsed: NotaFiscalParseada, nome_arquivo: str, usuario=None, *,
    clientes_cache: dict[str, Cliente] | None = None,
    chaves_importadas: set[str] | None = None,
    situacoes_sefaz: dict[str, sefaz.ResultadoConsultaSefaz] | None = None,
) -> ResultadoConfirmacao:
    """Mesma coisa que confirmar_importacao, a partir de um XML já parseado
    (ver montar_previa_parsed — mesmo motivo: não reparsear o lote inteiro
    duas vezes). `chaves_importadas`, se passado, é atualizado in-place a
    cada nota gravada com sucesso — é o que garante que duas notas com a
    mesma chave no mesmo lote ainda se pegam como duplicata sem voltar ao
    banco pra cada arquivo (ver prefetch_chaves_importadas).

    `situacoes_sefaz` segue o mesmo padrão de `clientes_cache` (ver
    prefetch_situacoes_sefaz) — reconsulta a SEFAZ nesta etapa mesmo que a
    prévia já tenha checado a mesma chave há pouco (reaproveitar entre as
    duas etapas do fluxo web de 2 passos fica pra depois, é só uma chamada
    a mais por nota, não um problema de corretude)."""
    identificacao = identificar_nota(parsed, clientes_cache=clientes_cache)

    if identificacao.cliente is None:
        return ResultadoConfirmacao(
            "cliente_pendente", cnpj_cliente=identificacao.cnpj_cliente,
            nome_cliente=identificacao.nome_cliente)
    ja_existe = (
        parsed.chave_acesso in chaves_importadas if chaves_importadas is not None
        else NotaFiscal.objects.filter(chave_acesso=parsed.chave_acesso).exists()
    )
    if ja_existe:
        return ResultadoConfirmacao("duplicada")

    resultado_sefaz = (
        situacoes_sefaz.get(parsed.chave_acesso) if situacoes_sefaz is not None
        else sefaz.consultar_situacao(parsed.chave_acesso, identificacao.centro_custo)
    )

    eh_entrada = identificacao.tipo == NotaFiscal.Tipo.ENTRADA
    situacao = situacao_da_nota(parsed, resultado_sefaz)
    valida = situacao == NotaFiscal.Situacao.VALIDA
    with transaction.atomic():
        nota = NotaFiscal.objects.create(
            cliente=identificacao.cliente, tipo=identificacao.tipo,
            chave_acesso=parsed.chave_acesso, n_nf=parsed.n_nf, serie=parsed.serie,
            nat_op=parsed.nat_op, data_emissao=parsed.data_emissao,
            emit_cnpj=parsed.emit_cnpj, emit_nome=parsed.emit_nome,
            dest_cnpj=parsed.dest_cnpj, dest_nome=parsed.dest_nome,
            centro_custo=identificacao.centro_custo, valor_total=parsed.valor_total,
            arquivo_origem=nome_arquivo, importado_por=usuario, xml_bruto=parsed.xml_bruto,
            inf_cpl=parsed.inf_cpl, ref_nfe="\n".join(parsed.ref_nfe),
            tp_nf=parsed.tp_nf, autorizada=parsed.autorizada, situacao=situacao,
            **_campos_verificacao_sefaz(resultado_sefaz, situacao),
        )
        NotaFiscalItem.objects.bulk_create([
            NotaFiscalItem(
                nota_fiscal=nota, n_item=item.n_item, c_prod=item.c_prod, x_prod=item.x_prod,
                ncm=item.ncm, cfop=item.cfop, u_com=item.u_com, q_com=item.q_com,
                v_un_com=item.v_un_com, v_prod=item.v_prod, inf_ad_prod=item.inf_ad_prod,
                tipo_retorno=item.tipo_retorno,
                # Saldo só é iniciado pra itens de entrada VALIDA dentro do
                # escopo controlado (tecido) — insumo de produção fica de
                # fora do controle de saldo por enquanto (ver
                # eh_ncm_controlado), e nota sem autorização/estorno não
                # cria saldo.
                saldo_atual=(item.q_com if eh_entrada and valida and eh_ncm_controlado(item.ncm)
                             else None),
            )
            for item in parsed.itens
        ])
        if situacao == NotaFiscal.Situacao.ESTORNO:
            matching.aplicar_estorno(nota)
        elif valida and not matching.aplicar_estornos_pendentes(nota) and not eh_entrada:
            matching.aplicar_baixas_da_nota(nota)
            matching.detectar_duplicidade(nota)

    if chaves_importadas is not None:
        chaves_importadas.add(parsed.chave_acesso)

    if eh_entrada and nota.situacao == NotaFiscal.Situacao.VALIDA:
        # Pendências que já tinham a referência certa, só esperando essa NF
        # existir, se resolvem sozinhas agora (ver matching.py).
        matching.reprocessar_pendencias_aguardando(nota)

    return ResultadoConfirmacao("importada", nota=nota)
