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
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify

from contas.decorators import setor_required
from contas.models import Setor

from . import excel_export, importador, matching, relatorio_pdf, servicos
from .forms import ResolverPendenciaForm, UploadXmlForm
from .models import AssociacaoProduto, NotaFiscal, NotaFiscalItem, PendenciaMatching
from .nfe_xml import XmlInvalido

_fiscal = setor_required(Setor.FISCAL, nome_area="Saldo Fiscal")

_SESSAO_TOKEN = "fiscal_import_token"
_IDADE_MAX_UPLOAD_ORFAO_SEGUNDOS = 60 * 60
# Cartões por página na tela de revisão (fiscal/templates/fiscal/_partials/
# previa_revisao.html) — sem isso, um lote de milhares de XMLs virava um DOM
# gigante e travava o navegador (o resumo no topo continua somando o lote
# inteiro, só os cartões é que são paginados).
_ITENS_POR_PAGINA_REVISAO = 20


def _contexto(secao: str, **extra) -> dict:
    """Contexto comum de toda página do módulo — qual item da sidebar fica
    marcado como ativo, se o submenu de Importação deve abrir expandido, e a
    contagem de pendências abertas pro aviso numérico ao lado do item
    "Pendências" (ver fiscal/templates/fiscal/base_fiscal.html)."""
    return {
        "secao_ativa": secao,
        "importacao_aberta": secao in {"importar_entrada", "importar_saida"},
        "pendencias_abertas": servicos.contar_pendencias_abertas(),
        **extra,
    }


# ─────────────────────────── Início (dashboard) ──────────────────────────
@login_required
@_fiscal
def index(request):
    itens = servicos.historico_itens(servicos.Filtros())
    maiores_saldos = sorted(itens, key=lambda l: l.saldo, reverse=True)[:8]
    maiores_consumos = sorted(itens, key=lambda l: l.utilizado, reverse=True)[:8]
    pendencias_recentes = (
        PendenciaMatching.objects.filter(resolvido=False)
        .select_related("saida_item", "saida_item__nota_fiscal").order_by("-criado_em")[:8]
    )
    return render(request, "fiscal/inicio.html", _contexto(
        "inicio", titulo_pagina="Saldo Fiscal", kpis=servicos.kpis_dashboard(),
        evolucao_json=servicos.evolucao_mensal(),
        centro_custo_json=servicos.saldo_por_centro_custo(),
        maiores_saldos=maiores_saldos, maiores_consumos=maiores_consumos,
        pendencias_recentes=pendencias_recentes,
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
    # O dropzone da tela (fiscal/templates/fiscal/_partials/upload_revisao.html)
    # manda cada novo lote de arquivos via fetch, sem navegar — esse header
    # (setado só por esse fetch, nunca por um POST normal de formulário) diz
    # pra devolver só o fragmento da prévia, não a página inteira.
    eh_preview_ajax = request.headers.get("X-Fiscal-Preview") == "1"

    if request.method != "POST":
        return render(request, template, _contexto(secao, titulo_pagina=titulo, form=UploadXmlForm()))

    arquivos = request.FILES.getlist("arquivos")
    if not arquivos:
        if eh_preview_ajax:
            return render(request, "fiscal/_partials/previa_revisao.html", {
                "previas": [], "pagina": Paginator([], _ITENS_POR_PAGINA_REVISAO).get_page(1),
                "token": "", "url_confirmar": url_confirmar,
                "resumo": {
                    "prontas": 0, "ja_cadastradas": 0, "invalidas": 0,
                    "por_unidade": {}, "valor_total": Decimal("0"), "total_arquivos": 0,
                }})
        messages.error(request, "Selecione pelo menos um arquivo XML.")
        return render(request, template, _contexto(secao, titulo_pagina=titulo, form=UploadXmlForm()))

    _limpar_uploads_orfaos()
    _limpar_upload_da_sessao(request)

    token = uuid.uuid4().hex
    pasta = _dir_uploads(token)

    # Passo 1: grava em disco + parseia cada XML (só CPU, sem tocar no banco
    # ainda). Separar isso da consulta ao banco é o que permite ir de "1
    # query por arquivo" pra "1 query pro lote inteiro" no passo 2 — com
    # lotes de milhares de XMLs, essa é a diferença entre a tela travar por
    # minutos ou responder em segundos.
    itens: list[dict] = []
    for arquivo in arquivos:
        conteudo = arquivo.read()
        (pasta / arquivo.name).write_bytes(conteudo)
        try:
            parsed = importador.parse_nfe(conteudo, arquivo.name)
        except XmlInvalido as e:
            itens.append({"nome_arquivo": arquivo.name, "erro": str(e)})
            continue
        itens.append({"nome_arquivo": arquivo.name, "parsed": parsed})

    lote_parseado = [it["parsed"] for it in itens if "parsed" in it]
    clientes_cache = importador.prefetch_clientes(lote_parseado)
    chaves_importadas = importador.prefetch_chaves_importadas(lote_parseado)

    previas = []
    for it in itens:
        if "erro" in it:
            previas.append(it)
            continue
        previa = importador.montar_previa_parsed(
            it["parsed"], it["nome_arquivo"],
            clientes_cache=clientes_cache, chaves_importadas=chaves_importadas)
        previas.append({
            "nome_arquivo": it["nome_arquivo"],
            "previa": previa,
            "tipo_diferente": previa.identificacao.tipo != tipo_esperado,
        })

    request.session[_SESSAO_TOKEN] = token
    por_unidade: dict[str, Decimal] = {}
    valor_total = Decimal("0")
    for p in previas:
        if "previa" not in p:
            continue
        valor_total += p["previa"].parsed.valor_total
        for item in p["previa"].itens:
            # Soma tudo que está na nota (KG, MT, o que for) — mesmo item
            # "Fora do escopo" (NCM ainda não controlado) entra aqui, esse
            # total é só informativo de quanto tá vindo na leva, não é o
            # mesmo escopo do saldo/baixa automática.
            por_unidade[item.unidade] = por_unidade.get(item.unidade, Decimal("0")) + item.quantidade
    resumo = {
        # Sempre sobre o LOTE INTEIRO (não só a página atual) — a paginação
        # abaixo é só de renderização, não muda o que está sendo revisado.
        "prontas": sum(
            1 for p in previas if "previa" in p and p["previa"].pode_confirmar),
        "ja_cadastradas": sum(1 for p in previas if "previa" in p and p["previa"].ja_importada),
        "invalidas": sum(1 for p in previas if "erro" in p),
        "por_unidade": por_unidade,
        "valor_total": valor_total,
        "total_arquivos": len(previas),
    }
    numero_pagina = request.POST.get("pagina") or request.GET.get("pagina") or 1
    paginador = Paginator(previas, _ITENS_POR_PAGINA_REVISAO)
    pagina = paginador.get_page(numero_pagina)
    contexto_revisao = {
        "previas": pagina.object_list, "pagina": pagina,
        # Django template não deixa chamar método com kwargs no {% for %}
        # (get_elided_page_range(on_each_side=..., on_ends=...)) — resolvido
        # aqui em vez de no template.
        "paginas_elided": list(paginador.get_elided_page_range(pagina.number, on_each_side=1, on_ends=1)),
        "token": token, "url_confirmar": url_confirmar, "resumo": resumo,
    }
    if eh_preview_ajax:
        return render(request, "fiscal/_partials/previa_revisao.html", contexto_revisao)
    return render(request, template, _contexto(
        secao, titulo_pagina=titulo, revisao=True, **contexto_revisao,
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

    # Mesma ideia de duas passadas da prévia (_etapa1_upload): parseia tudo
    # primeiro (sem banco), pré-carrega cliente/duplicata do lote inteiro em
    # 2 queries, e só então confirma nota por nota (cada uma na própria
    # transação — uma nota ruim não derruba as outras já gravadas).
    itens: list[dict] = []
    for caminho in arquivos:
        conteudo = caminho.read_bytes()
        try:
            parsed = importador.parse_nfe(conteudo, caminho.name)
        except XmlInvalido as e:
            itens.append({"nome_arquivo": caminho.name, "erro": str(e)})
            continue
        itens.append({"nome_arquivo": caminho.name, "parsed": parsed})

    lote_parseado = [it["parsed"] for it in itens if "parsed" in it]
    clientes_cache = importador.prefetch_clientes(lote_parseado)
    chaves_importadas = importador.prefetch_chaves_importadas(lote_parseado)

    importadas = duplicadas = 0
    pendentes_cliente: set[str] = set()
    erros = []
    for it in itens:
        if "erro" in it:
            erros.append(f"{it['nome_arquivo']}: {it['erro']}")
            continue
        resultado = importador.confirmar_importacao_parsed(
            it["parsed"], it["nome_arquivo"], usuario=request.user,
            clientes_cache=clientes_cache, chaves_importadas=chaves_importadas)
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


@login_required
@_fiscal
def historico_xlsx(request):
    filtros = servicos.filtros_da_query(request.GET)
    conteudo = excel_export.gerar_xlsx_historico(servicos.historico_itens(filtros))
    resp = HttpResponse(
        conteudo, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="historico-fiscal.xlsx"'
    return resp


@login_required
@_fiscal
def historico_pdf(request):
    filtros = servicos.filtros_da_query(request.GET)
    itens = servicos.historico_itens(filtros)
    periodo = f"{filtros.data_inicio or '—'} a {filtros.data_fim or '—'}"
    conteudo = relatorio_pdf.gerar_pdf_historico(
        periodo_label=periodo, filtros=filtros.centro_custo or "Todos os centros de custo",
        itens=itens, totais=_totais_historico(itens))
    resp = HttpResponse(conteudo, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="historico-fiscal.pdf"'
    return resp


# ─────────────────────────────── Histórico ───────────────────────────────
# Histórico e Conciliação eram duas telas fazendo a mesma coisa — viraram
# uma só, com a mesma estrutura do protótipo do fiscal: toggle "por NF de
# entrada" / "por item de saída", chips de status, tabela com KG/MT e R$
# (comparativo pelo custo de entrada) lado a lado, e um modal de detalhe
# com o "rolo" de consumo ao clicar numa linha (ver historico_detalhe).
def _totais_historico(itens: list) -> dict:
    return {
        "utilizado": sum((l.utilizado for l in itens), Decimal("0")),
        "saldo": sum((l.saldo for l in itens), Decimal("0")),
        "valor_entrada": sum((l.valor_entrada for l in itens), Decimal("0")),
        "valor_utilizado": sum((l.valor_utilizado for l in itens), Decimal("0")),
        "valor_saldo": sum((l.valor_saldo for l in itens), Decimal("0")),
    }


@login_required
@_fiscal
def historico(request):
    filtros = servicos.filtros_da_query(request.GET)
    modo = request.GET.get("modo", "entrada")

    if modo == "saida":
        return render(request, "fiscal/historico.html", _contexto(
            "historico", titulo_pagina="Histórico", modo=modo, filtros=filtros,
            itens_saida=servicos.historico_saida_itens(filtros),
            opcoes_centro_custo=servicos.opcoes_centro_custo(),
        ))

    status_filtro = request.GET.get("status", "")
    todos_itens = servicos.historico_itens(filtros)
    contagem_status = {chave: 0 for chave in servicos.STATUS_HISTORICO}
    for linha in todos_itens:
        contagem_status[linha.status] += 1
    itens = [l for l in todos_itens if l.status == status_filtro] if status_filtro else todos_itens
    chips_status = [
        (chave, rotulo, contagem_status[chave]) for chave, rotulo in servicos.STATUS_HISTORICO.items()
    ]
    return render(request, "fiscal/historico.html", _contexto(
        "historico", titulo_pagina="Histórico", modo=modo, itens=itens, filtros=filtros,
        status_filtro=status_filtro, chips_status=chips_status, total_itens=len(todos_itens),
        totais=_totais_historico(itens), opcoes_centro_custo=servicos.opcoes_centro_custo(),
    ))


@login_required
@_fiscal
def historico_detalhe(request, item_id: int):
    """Conteúdo do modal de detalhe (ver fiscal/static/fiscal/js — abrir via
    fetch e mostrar o overlay). Chave de acesso formatada em grupos de 4
    dígitos, igual ao protótipo original."""
    entrada_item = get_object_or_404(
        NotaFiscalItem, pk=item_id, nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA)
    vinculos = list(servicos.vinculos_do_item(entrada_item))
    saldo_bruto = entrada_item.saldo_atual or 0
    saldo = max(saldo_bruto, 0)
    excedido = -saldo_bruto if saldo_bruto < 0 else 0
    utilizado = entrada_item.q_com - saldo_bruto
    pct_utilizado = round(float(utilizado / entrada_item.q_com * 100), 1) if entrada_item.q_com else 0.0
    pct_disponivel = round(100 - pct_utilizado, 1) if not excedido else 0.0
    chave = entrada_item.nota_fiscal.chave_acesso
    chave_formatada = " ".join(chave[i:i + 4] for i in range(0, len(chave), 4))
    status = servicos.classificar_status_entrada(
        entrada_item, servicos.tem_pendencia_aberta(entrada_item.nota_fiscal))
    vinculos_com_valor = [(v, v.quantidade_baixada * entrada_item.v_un_com) for v in vinculos]
    return render(request, "fiscal/_partials/historico_expander.html", {
        "item": entrada_item, "vinculos": vinculos, "vinculos_com_valor": vinculos_com_valor,
        "saldo": saldo, "excedido": excedido,
        "utilizado": utilizado, "pct_utilizado": pct_utilizado, "pct_disponivel": pct_disponivel,
        "chave_formatada": chave_formatada, "status": status,
        "valor_recebido": entrada_item.q_com * entrada_item.v_un_com,
        "valor_utilizado": utilizado * entrada_item.v_un_com,
        "valor_saldo": saldo * entrada_item.v_un_com,
        "pendencias": servicos.pendencias_da_entrada(entrada_item.nota_fiscal),
    })


# ─────────────────────────── Saldo de Tecidos ─────────────────────────────
@login_required
@_fiscal
def saldo_tecidos(request):
    filtros = servicos.filtros_da_query(request.GET)
    agrupar_por = request.GET.get("agrupar", "descricao")
    so_com_saldo = request.GET.get("so_com_saldo") == "1"
    produtos = servicos.saldo_por_tecido(filtros, agrupar_por=agrupar_por, so_com_saldo=so_com_saldo)
    return render(request, "fiscal/saldo_tecidos.html", _contexto(
        "saldo_tecidos", titulo_pagina="Saldo de Tecidos", filtros=filtros, produtos=produtos,
        agrupar_por=agrupar_por, so_com_saldo=so_com_saldo,
        resumo_unidades=servicos.resumo_saldo_por_unidade(produtos),
        opcoes_centro_custo=servicos.opcoes_centro_custo(),
    ))


@login_required
@_fiscal
def saldo_tecidos_detalhe(request):
    filtros = servicos.filtros_da_query(request.GET)
    agrupar_por = request.GET.get("agrupar", "descricao")
    chave = request.GET.get("chave", "")
    itens = servicos.itens_do_tecido(filtros, chave, agrupar_por)
    total_recebido = sum((i.q_com for i in itens), Decimal("0"))
    total_saldo = sum((max(i.saldo_atual or Decimal("0"), Decimal("0")) for i in itens), Decimal("0"))
    return render(request, "fiscal/_partials/saldo_tecidos_expander.html", {
        "chave": chave, "itens": itens, "total_recebido": total_recebido, "total_saldo": total_saldo,
    })


# ─────────────────────────────── Pendências ──────────────────────────────
@login_required
@_fiscal
def pendencias(request):
    """A "Divergências" do protótipo do fiscal — reúne todos os motivos de
    pendência num lugar só, com chips por tipo (a gente já usa a mesma
    tabela `PendenciaMatching` pra todos, incluindo excesso de saldo, então
    não precisou de tela separada)."""
    todas = list(
        PendenciaMatching.objects.filter(resolvido=False)
        .select_related("saida_item", "saida_item__nota_fiscal", "saida_item__nota_fiscal__cliente")
    )
    motivo_filtro = request.GET.get("motivo", "")
    contagem_motivo: dict[str, int] = {}
    for p in todas:
        contagem_motivo[p.motivo] = contagem_motivo.get(p.motivo, 0) + 1
    chips_motivo = [
        (valor, label, contagem_motivo.get(valor, 0))
        for valor, label in PendenciaMatching.Motivo.choices if contagem_motivo.get(valor, 0)
    ]
    fila = [p for p in todas if p.motivo == motivo_filtro] if motivo_filtro else todas
    return render(request, "fiscal/pendencias.html", _contexto(
        "pendencias", titulo_pagina="Pendências", pendencias=fila,
        total_pendencias=len(todas), motivo_filtro=motivo_filtro, chips_motivo=chips_motivo,
    ))


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
        if form.cleaned_data.get("lembrar_associacao") and pendencia.saida_item.c_prod and entrada_item.c_prod:
            AssociacaoProduto.objects.update_or_create(
                cliente=pendencia.saida_item.nota_fiscal.cliente, cprod_saida=pendencia.saida_item.c_prod,
                defaults={"cprod_entrada": entrada_item.c_prod, "criado_por": request.user},
            )
            mensagem += f' Código "{pendencia.saida_item.c_prod}" lembrado pra próxima vez.'
    else:
        mensagem = "Justificativa: " + form.cleaned_data["ignorar_com_justificativa"]

    pendencia.resolvido = True
    pendencia.resolvido_por = request.user
    pendencia.detalhe += f"\n\nResolução: {mensagem}"
    pendencia.resolvido_em = timezone.now()
    pendencia.save(update_fields=["resolvido", "resolvido_por", "resolvido_em", "detalhe"])

    messages.success(request, "Pendência resolvida.")
    return redirect("fiscal:pendencias")
