"""
Views do Saldo Fiscal. Toda view carrega @login_required + @setor_required —
o módulo é registrado em paineis/modulos.py com `url_externa` (abre em nova
aba, layout próprio, não herda o shell da Central — ver
fiscal/templates/fiscal/base_fiscal.html), então o SetorAccessMiddleware da
Central NÃO cobre essas URLs automaticamente. Cada view precisa se proteger
sozinha — é por isso que o decorator aparece em toda função aqui, mesmo
repetitivo, e não deve ser removido "pra simplificar"."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify

from contas.decorators import setor_required
from contas.models import Setor

from . import excel_export, importador, matching, relatorio_pdf, servicos
from .forms import ResolverPendenciaForm, UploadXmlForm
from .models import NotaFiscal, NotaFiscalItem, PendenciaMatching
from .nfe_xml import XmlInvalido

_fiscal = setor_required(Setor.FISCAL, nome_area="Saldo Fiscal")

_SESSAO_TOKEN = "fiscal_import_token"
_IDADE_MAX_UPLOAD_ORFAO_SEGUNDOS = 60 * 60


def _contexto(secao: str, **extra) -> dict:
    """Contexto comum de toda página do módulo — qual item da sidebar fica
    marcado como ativo (ver fiscal/templates/fiscal/base_fiscal.html) e se o
    submenu de Importação deve abrir expandido."""
    return {
        "secao_ativa": secao,
        "importacao_aberta": secao in {"importar_entrada", "importar_saida"},
        **extra,
    }


# ─────────────────────────── Início (dashboard) ──────────────────────────
@login_required
@_fiscal
def index(request):
    kpis = servicos.kpis_dashboard()
    return render(request, "fiscal/inicio.html", _contexto(
        "inicio", titulo_pagina="Saldo Fiscal", kpis=kpis,
        evolucao_json=servicos.evolucao_mensal(),
        centro_custo_json=servicos.saldo_por_centro_custo(),
    ))


# ───────────────────────────── Upload de XML ─────────────────────────────
def _dir_uploads(token: str) -> Path:
    caminho = Path(settings.BASE_DIR) / "cache" / "fiscal_uploads" / token
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def _limpar_uploads_orfaos() -> None:
    base = Path(settings.BASE_DIR) / "cache" / "fiscal_uploads"
    if not base.exists():
        return
    agora = time.time()
    for pasta in base.iterdir():
        try:
            if pasta.is_dir() and agora - pasta.stat().st_mtime > _IDADE_MAX_UPLOAD_ORFAO_SEGUNDOS:
                for arquivo in pasta.glob("*.xml"):
                    arquivo.unlink(missing_ok=True)
                pasta.rmdir()
        except OSError:
            pass  # outro request já está limpando a mesma pasta — sem problema


def _limpar_upload_da_sessao(request) -> None:
    token = request.session.pop(_SESSAO_TOKEN, None)
    if not token:
        return
    pasta = _dir_uploads(token)
    for arquivo in pasta.glob("*.xml"):
        arquivo.unlink(missing_ok=True)
    try:
        pasta.rmdir()
    except OSError:
        pass


def _etapa1_upload(request, *, secao: str, tipo_esperado: str, template: str, titulo: str,
                    url_confirmar: str):
    """Passo 1 do upload (GET mostra o form, POST processa e mostra a
    prévia): nada é gravado ainda — só o parse + simulação do casamento
    automático (ver importador.montar_previa). `tipo_esperado` só serve pra
    avisar o usuário se o arquivo parece ter sido enviado na tela errada; o
    tipo de verdade é decidido pelo próprio XML (CNPJ emit/dest), não pela
    tela usada."""
    if request.method != "POST":
        return render(request, template, _contexto(secao, titulo_pagina=titulo, form=UploadXmlForm()))

    arquivos = request.FILES.getlist("arquivos")
    if not arquivos:
        messages.error(request, "Selecione pelo menos um arquivo XML.")
        return render(request, template, _contexto(secao, titulo_pagina=titulo, form=UploadXmlForm()))

    _limpar_uploads_orfaos()
    _limpar_upload_da_sessao(request)

    token = uuid.uuid4().hex
    pasta = _dir_uploads(token)
    previas = []
    for arquivo in arquivos:
        conteudo = arquivo.read()
        (pasta / arquivo.name).write_bytes(conteudo)
        try:
            previa = importador.montar_previa(conteudo, arquivo.name)
        except XmlInvalido as e:
            previas.append({"nome_arquivo": arquivo.name, "erro": str(e)})
            continue
        previas.append({
            "nome_arquivo": arquivo.name,
            "previa": previa,
            "tipo_diferente": previa.identificacao.tipo != tipo_esperado,
        })

    request.session[_SESSAO_TOKEN] = token
    return render(request, template, _contexto(
        secao, titulo_pagina=titulo, previas=previas, token=token,
        revisao=True, url_confirmar=url_confirmar,
    ))


def _etapa2_confirmar(request, *, url_voltar: str):
    """Passo 2 (só POST): reprocessa os arquivos staged em disco e grava de
    verdade. Idempotente — arquivo já importado antes vira 'duplicada' no
    resumo, não erro."""
    if request.method != "POST":
        return redirect(url_voltar)

    token_sessao = request.session.get(_SESSAO_TOKEN)
    if not token_sessao or token_sessao != request.POST.get("token"):
        messages.error(request, "Sessão de importação expirada ou inválida — envie os arquivos de novo.")
        return redirect(url_voltar)

    if request.POST.get("acao") == "cancelar":
        _limpar_upload_da_sessao(request)
        messages.info(request, "Importação cancelada — nenhum dado foi alterado.")
        return redirect(url_voltar)

    pasta = _dir_uploads(token_sessao)
    arquivos = sorted(pasta.glob("*.xml"))
    if not arquivos:
        _limpar_upload_da_sessao(request)
        messages.error(request, "Os arquivos enviados não estão mais disponíveis — envie de novo.")
        return redirect(url_voltar)

    importadas = duplicadas = 0
    pendentes_cliente: set[str] = set()
    erros = []
    for caminho in arquivos:
        try:
            resultado = importador.confirmar_importacao(
                caminho.read_bytes(), caminho.name, usuario=request.user)
        except XmlInvalido as e:
            erros.append(f"{caminho.name}: {e}")
            continue
        if resultado.status == "importada":
            importadas += 1
        elif resultado.status == "duplicada":
            duplicadas += 1
        elif resultado.status == "cliente_pendente":
            pendentes_cliente.add(f"{resultado.nome_cliente} (CNPJ {resultado.cnpj_cliente})")

    _limpar_upload_da_sessao(request)

    if importadas:
        messages.success(request, f"{importadas} nota(s) importada(s) com sucesso.")
    if duplicadas:
        messages.info(request, f"{duplicadas} nota(s) já tinham sido importadas antes — ignoradas.")
    if pendentes_cliente:
        messages.warning(
            request,
            "CNPJ sem cliente cadastrado, nada foi importado desses arquivos: "
            + "; ".join(sorted(pendentes_cliente))
            + ". Cadastre o cliente no admin e envie de novo.")
    if erros:
        messages.error(request, "Falha ao processar: " + "; ".join(erros))
    if not (importadas or duplicadas or pendentes_cliente or erros):
        messages.info(request, "Nada foi importado.")

    return redirect(url_voltar)


@login_required
@_fiscal
def importar_entrada(request):
    return _etapa1_upload(
        request, secao="importar_entrada", tipo_esperado=NotaFiscal.Tipo.ENTRADA,
        template="fiscal/importar_entrada.html", titulo="Importar NF de Entrada",
        url_confirmar="fiscal:confirmar_importacao_entrada")


@login_required
@_fiscal
def confirmar_importacao_entrada(request):
    return _etapa2_confirmar(request, url_voltar="fiscal:importar_entrada")


@login_required
@_fiscal
def importar_saida(request):
    return _etapa1_upload(
        request, secao="importar_saida", tipo_esperado=NotaFiscal.Tipo.SAIDA,
        template="fiscal/importar_saida.html", titulo="Importar NF de Saída",
        url_confirmar="fiscal:confirmar_importacao_saida")


@login_required
@_fiscal
def confirmar_importacao_saida(request):
    return _etapa2_confirmar(request, url_voltar="fiscal:importar_saida")


# ────────────────────────────── Relatórios ───────────────────────────────
def _itens_relatorio(filtros: servicos.Filtros) -> list[dict]:
    return [
        {
            "cliente": item.nota_fiscal.cliente.nome, "nf": item.nota_fiscal.n_nf,
            "produto": item.x_prod, "ncm": item.ncm, "unidade": item.u_com,
            "recebido": item.q_com, "saldo": item.saldo_atual or 0,
            "pct": item.percentual_consumido or 0.0,
        }
        for item in servicos.saldo_por_produto(filtros)
    ]


@login_required
@_fiscal
def relatorios(request):
    filtros = servicos.filtros_da_query(request.GET)
    itens = _itens_relatorio(filtros)
    return render(request, "fiscal/relatorios.html", _contexto(
        "relatorios", titulo_pagina="Relatórios", filtros=filtros, itens=itens[:500],
        total_itens=len(itens), opcoes_centro_custo=servicos.opcoes_centro_custo(),
    ))


@login_required
@_fiscal
def relatorio_saldo_pdf(request):
    filtros = servicos.filtros_da_query(request.GET)
    itens = _itens_relatorio(filtros)
    kpis_res = servicos.kpis_dashboard()
    kpis = [
        ("Recebido total", f"{kpis_res.recebido_total:,.2f}".replace(",", ".")),
        ("Saldo atual", f"{kpis_res.saldo_total:,.2f}".replace(",", ".")),
        ("% Consumido", f"{kpis_res.pct_consumo:.1f}%"),
    ]
    periodo = f"{filtros.data_inicio or '—'} a {filtros.data_fim or '—'}"
    conteudo = relatorio_pdf.gerar_pdf_saldo(
        periodo_label=periodo, filtros=filtros.centro_custo or "Todos os centros de custo",
        kpis=kpis, itens=itens)
    resp = HttpResponse(conteudo, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="saldo-fiscal-{slugify(periodo)}.pdf"'
    return resp


@login_required
@_fiscal
def relatorio_saldo_xlsx(request):
    filtros = servicos.filtros_da_query(request.GET)
    conteudo = excel_export.gerar_xlsx_saldo(_itens_relatorio(filtros))
    resp = HttpResponse(
        conteudo, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="saldo-fiscal.xlsx"'
    return resp


# ─────────────────────────────── Histórico ───────────────────────────────
@login_required
@_fiscal
def historico(request):
    filtros = servicos.filtros_da_query(request.GET)
    return render(request, "fiscal/historico.html", _contexto(
        "historico", titulo_pagina="Histórico", carteira=servicos.carteira_entradas(filtros),
        filtros=filtros, opcoes_centro_custo=servicos.opcoes_centro_custo(),
    ))


@login_required
@_fiscal
def historico_detalhe(request, nota_id: int):
    nota = get_object_or_404(NotaFiscal, pk=nota_id, tipo=NotaFiscal.Tipo.ENTRADA)
    return render(request, "fiscal/_partials/historico_expander.html", {
        "nota": nota, "itens": nota.itens.all(), "movimentacao": servicos.movimentacao_da_nota(nota),
    })


# ─────────────────────────────── Pendências ──────────────────────────────
@login_required
@_fiscal
def pendencias(request):
    fila = (
        PendenciaMatching.objects.filter(resolvido=False)
        .select_related("saida_item", "saida_item__nota_fiscal", "saida_item__nota_fiscal__cliente")
    )
    return render(request, "fiscal/pendencias.html", _contexto(
        "pendencias", titulo_pagina="Pendências", pendencias=fila))


@login_required
@_fiscal
def resolver_pendencia(request, pendencia_id: int):
    pendencia = get_object_or_404(PendenciaMatching, pk=pendencia_id, resolvido=False)
    if request.method != "POST":
        candidatos = []
        if pendencia.saida_item:
            candidatos = list(NotaFiscalItem.objects.filter(
                nota_fiscal__cliente=pendencia.saida_item.nota_fiscal.cliente,
                nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA, saldo_atual__gt=0,
            ).select_related("nota_fiscal")[:50])
        return render(request, "fiscal/resolver_pendencia.html", _contexto(
            "pendencias", titulo_pagina="Resolver pendência",
            pendencia=pendencia, candidatos=candidatos, form=ResolverPendenciaForm(),
        ))

    form = ResolverPendenciaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escolha um item de entrada ou informe a justificativa.")
        return redirect("fiscal:pendencias")

    entrada_item_id = form.cleaned_data.get("entrada_item_id")
    if entrada_item_id:
        entrada_item = get_object_or_404(NotaFiscalItem, pk=entrada_item_id)
        matching.aplicar_baixa(pendencia.saida_item, entrada_item)
        mensagem = f"Baixa aplicada manualmente contra a NF {entrada_item.nota_fiscal.n_nf}."
    else:
        mensagem = "Justificativa: " + form.cleaned_data["ignorar_com_justificativa"]

    pendencia.resolvido = True
    pendencia.resolvido_por = request.user
    pendencia.detalhe += f"\n\nResolução: {mensagem}"
    pendencia.resolvido_em = timezone.now()
    pendencia.save(update_fields=["resolvido", "resolvido_por", "resolvido_em", "detalhe"])

    messages.success(request, "Pendência resolvida.")
    return redirect("fiscal:pendencias")
