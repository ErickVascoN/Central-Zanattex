from __future__ import annotations

import time
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.text import slugify

from contas.decorators import admin_required
from integracao.db_sync import sync_dataframe

from . import servicos, relatorio_pdf, importador
from .forms import ImportarExcelForm
from .models import ImportacaoCarteira

# Chaves de sessão que amarram as duas etapas do upload (arquivo salvo em
# disco -> resumo de conferência -> confirmar) ao mesmo navegador. Nenhum
# dado da carteira em si passa pela sessão, só o nome do token/arquivo.
_SESSAO_TOKEN = "carteira_import_token"
_SESSAO_NOME_ARQUIVO = "carteira_import_nome_arquivo"

# Upload abandonado (usuário fechou a aba antes de confirmar/cancelar) fica
# órfão em disco — varrido na entrada do próximo upload por qualquer
# usuário, não precisa de cron/tarefa agendada à parte.
_IDADE_MAX_UPLOAD_ORFAO_SEGUNDOS = 60 * 60


def _dir_uploads() -> Path:
    caminho = Path(settings.BASE_DIR) / "cache" / "carteira_uploads"
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def _limpar_uploads_orfaos() -> None:
    agora = time.time()
    for arquivo in _dir_uploads().glob("*.xlsx"):
        try:
            if agora - arquivo.stat().st_mtime > _IDADE_MAX_UPLOAD_ORFAO_SEGUNDOS:
                arquivo.unlink()
        except OSError:
            pass  # outro request já removeu/está removendo o mesmo arquivo — sem problema


def _limpar_upload_da_sessao(request) -> None:
    """Remove o arquivo temporário da tentativa de upload em andamento (se
    houver) e esvazia as chaves de sessão — usado tanto ao cancelar quanto
    depois de confirmar com sucesso."""
    token = request.session.pop(_SESSAO_TOKEN, None)
    request.session.pop(_SESSAO_NOME_ARQUIVO, None)
    if token:
        (_dir_uploads() / f"{token}.xlsx").unlink(missing_ok=True)


def _montar_resumo(resultado: importador.ResultadoImportacao, nome_arquivo: str) -> dict:
    df = resultado.df
    return {
        "nome_arquivo": nome_arquivo,
        "linhas_importadas": resultado.linhas_importadas,
        "linhas_ignoradas": resultado.linhas_ignoradas,
        "avisos": resultado.avisos[:50],
        "avisos_ocultos": max(len(resultado.avisos) - 50, 0),
        "data_min": df["DATA"].min().date() if not df.empty else None,
        "data_max": df["DATA"].max().date() if not df.empty else None,
        "valor_total": servicos._fmt_r(df["VALOR_TOTAL"].sum()) if not df.empty else "R$ 0",
        "pedidos_distintos": int(df["PEDIDO"].nunique()) if not df.empty else 0,
    }


@login_required
@admin_required("Carteira de Pedidos")
def importar_excel(request):
    """Etapa 1: recebe o arquivo, faz o parse e mostra um RESUMO de
    conferência — nada é gravado ainda (ver confirmar_importacao). Reduz o
    risco de "subi o arquivo errado" sem querer, já que o upload substitui
    a carteira inteira."""
    historico = ImportacaoCarteira.objects.select_related("usuario")[:10]

    if request.method == "POST":
        form = ImportarExcelForm(request.POST, request.FILES)
        if not form.is_valid():
            for erro in form.errors.get("arquivo", []):
                messages.error(request, erro)
            return render(request, "carteira/importar.html", {
                "titulo_pagina": "Importar Carteira via Excel", "form": form, "historico": historico,
            })

        arquivo = form.cleaned_data["arquivo"]
        _limpar_uploads_orfaos()
        _limpar_upload_da_sessao(request)  # troca de arquivo no meio de uma tentativa anterior

        token = uuid.uuid4().hex
        caminho = _dir_uploads() / f"{token}.xlsx"
        with open(caminho, "wb") as destino:
            for pedaco in arquivo.chunks():
                destino.write(pedaco)

        try:
            resultado = importador.parse_excel_carteira(caminho)
        except importador.CabecalhoNaoEncontrado as e:
            caminho.unlink(missing_ok=True)
            messages.error(request, str(e))
            return render(request, "carteira/importar.html", {
                "titulo_pagina": "Importar Carteira via Excel", "form": ImportarExcelForm(), "historico": historico,
            })

        if resultado.df.empty:
            caminho.unlink(missing_ok=True)
            messages.error(
                request,
                "Nenhuma linha válida encontrada no arquivo "
                f"({resultado.linhas_ignoradas} ignorada(s) — confira os motivos abaixo).")
            return render(request, "carteira/importar.html", {
                "titulo_pagina": "Importar Carteira via Excel", "form": ImportarExcelForm(),
                "historico": historico, "avisos_falha": resultado.avisos[:50],
            })

        request.session[_SESSAO_TOKEN] = token
        request.session[_SESSAO_NOME_ARQUIVO] = arquivo.name
        return render(request, "carteira/importar.html", {
            "titulo_pagina": "Importar Carteira via Excel",
            "resumo": _montar_resumo(resultado, arquivo.name), "token": token, "historico": historico,
        })

    return render(request, "carteira/importar.html", {
        "titulo_pagina": "Importar Carteira via Excel", "form": ImportarExcelForm(), "historico": historico,
    })


@login_required
@admin_required("Carteira de Pedidos")
def confirmar_importacao(request):
    """Etapa 2 — só aceita POST, e só do mesmo token que a etapa 1 guardou
    na sessão (não dá pra confirmar um upload de outra aba/sessão trocando
    o valor do campo escondido)."""
    if request.method != "POST":
        return redirect("carteira:importar_excel")

    token_sessao = request.session.get(_SESSAO_TOKEN)
    token_post = request.POST.get("token")
    if not token_sessao or token_sessao != token_post:
        messages.error(request, "Sessão de importação expirada ou inválida — envie o arquivo de novo.")
        return redirect("carteira:importar_excel")

    if request.POST.get("acao") == "cancelar":
        _limpar_upload_da_sessao(request)
        messages.info(request, "Importação cancelada — nenhum dado foi alterado.")
        return redirect("carteira:importar_excel")

    caminho = _dir_uploads() / f"{token_sessao}.xlsx"
    if not caminho.exists():
        _limpar_upload_da_sessao(request)
        messages.error(request, "O arquivo enviado não está mais disponível — envie de novo.")
        return redirect("carteira:importar_excel")

    # Reprocessa o mesmo arquivo em vez de guardar o DataFrame inteiro na
    # sessão entre as duas etapas — é rápido (poucas centenas/milhares de
    # linhas) e mantém a sessão leve.
    nome_arquivo = request.session.get(_SESSAO_NOME_ARQUIVO, caminho.name)
    resultado = importador.parse_excel_carteira(caminho)

    if resultado.df.empty:
        messages.error(request, "O arquivo não tem mais linhas válidas — confira e envie de novo.")
        return redirect("carteira:importar_excel")

    ok = sync_dataframe(
        "carteira_pedidos", "carteira_pedidos", resultado.df,
        label="Carteira de Pedidos (upload manual)")
    if not ok:
        messages.error(request, "Não consegui gravar os dados agora — tente novamente em instantes.")
        return redirect("carteira:importar_excel")

    ImportacaoCarteira.objects.create(
        usuario=request.user, nome_arquivo=nome_arquivo,
        linhas_importadas=resultado.linhas_importadas,
        linhas_ignoradas=resultado.linhas_ignoradas, avisos=resultado.avisos,
    )
    _limpar_upload_da_sessao(request)
    messages.success(
        request,
        f'Carteira atualizada: {resultado.linhas_importadas} linha(s) importada(s) de "{nome_arquivo}"'
        + (f", {resultado.linhas_ignoradas} ignorada(s)." if resultado.linhas_ignoradas else "."))
    return redirect("carteira:importar_excel")


@login_required
@admin_required("Carteira de Pedidos")
def dashboard(request):
    """Dashboard de Carteira de Pedidos — mesmos indicadores/filtros do
    original (Streamlit), visual novo."""
    df = servicos.carregar_carteira()
    if df.empty:
        return render(request, "carteira/dashboard.html", {
            "titulo_pagina": "Carteira de Pedidos", "sem_dados": True,
        })

    sel = {c["name"]: request.GET.getlist(c["name"]) for c in servicos.campos_filtro(df)}
    prep = servicos.preparar_filtros(df, sel)
    opcoes = prep["opcoes"]

    anos_sel = [int(a) for a in sel["anos"] if a in opcoes["anos"]]
    if not anos_sel:
        anos_sel = [int(a) for a in opcoes["anos"]]  # default: todos (igual ao original)
    meses_sel = [m for m in sel["meses"] if m in opcoes["meses"]]
    clientes_sel = [v for v in sel["clientes"] if v in opcoes["clientes"]]
    categorias_sel = [v for v in sel["categorias"] if v in opcoes["categorias"]]
    produtos_sel = [v for v in sel["produtos"] if v in opcoes["produtos"]]
    tamanhos_sel = [v for v in sel["tamanhos"] if v in opcoes["tamanhos"]]
    estados_sel = [v for v in sel["estados"] if v in opcoes["estados"]]
    cc_sel = [v for v in sel["centros_custo"] if v in opcoes["centros_custo"]]

    df_f = servicos.aplicar_filtros(
        df, anos=anos_sel, meses=meses_sel, clientes=clientes_sel, categorias=categorias_sel,
        produtos=produtos_sel, tamanhos=tamanhos_sel, estados=estados_sel, centros_custo=cc_sel,
    )

    if df_f.empty:
        return render(request, "carteira/dashboard.html", {
            "titulo_pagina": "Carteira de Pedidos", "sem_dados": False, "sem_resultado": True,
            "total_itens": len(df),
            "filtros": prep["filtros"], "matriz": prep["matriz"],
        })

    kpis = servicos.kpis(df_f)
    resumo_cli = servicos.resumo_por_cliente(df_f, kpis["total_valor"])
    busca = request.GET.get("busca", "").strip()

    contexto = {
        "titulo_pagina": "Carteira de Pedidos",
        "sem_dados": False, "sem_resultado": False,
        "total_itens": len(df),
        "filtros": prep["filtros"], "matriz": prep["matriz"],
        "filtros_ativos": bool(meses_sel or clientes_sel or categorias_sel or produtos_sel
                               or tamanhos_sel or estados_sel or cc_sel
                               or len(anos_sel) != len(opcoes["anos"])),
        "busca": busca,
        "kpis": kpis,
        "pecas_categoria": servicos.pecas_por_categoria(df_f),
        "detalhe_categoria_json": servicos.detalhe_categoria_cliente(df_f),
        "detalhe_outros_json": servicos.detalhe_outros_produtos(df_f),
        "evolucao_json": servicos.evolucao_mensal(df_f),
        "categoria_valor_json": servicos.por_categoria_valor(df_f),
        "cliente_valor_json": servicos.por_cliente_valor(df_f),
        "detalhe_cliente_json": servicos.detalhe_cliente_categoria(df_f),
        "estado_valor_json": servicos.por_estado_valor(df_f),
        "centro_custo_json": servicos.por_centro_custo(df_f),
        "cliente_categoria_json": servicos.cliente_x_categoria(df_f),
        "tamanho_json": servicos.por_tamanho(df_f),
        "evolucao_categoria_json": servicos.evolucao_por_categoria(df_f),
        "heatmap_json": servicos.heatmap_cliente_mes(df_f),
        "top_produtos_json": servicos.top_produtos(df_f),
        "resumo_cliente": resumo_cli,
        "abc": servicos.curva_abc(resumo_cli, kpis["total_valor"]),
    }
    _detalhe = servicos.detalhe_itens(df_f, busca)
    contexto["detalhe"] = _detalhe[:300]
    contexto["detalhe_total"] = len(_detalhe)
    return render(request, "carteira/dashboard.html", contexto)


def _pdf_response(request, conteudo: bytes, nome: str):
    # ?dl=1: o app instalado (PWA) manda isso pra forçar download nativo — no
    # modo standalone não tem barra do navegador, então "inline" abre o PDF
    # sem nenhum jeito de imprimir/salvar (ver templates/base.html).
    disposicao = "attachment" if request.GET.get("dl") == "1" else "inline"
    resp = HttpResponse(conteudo, content_type="application/pdf")
    resp["Content-Disposition"] = f'{disposicao}; filename="{nome}"'
    return resp


@login_required
@admin_required("Carteira de Pedidos")
def relatorio_pdf_view(request):
    """Relatório PDF de Carteira de Pedidos — mesmos filtros e indicadores
    do dashboard, no design da nova Central."""
    df = servicos.carregar_carteira()
    if df.empty:
        return HttpResponse("Sem dados de carteira para gerar o relatório.", status=404)

    opcoes = servicos.opcoes_filtro(df)
    anos_sel = [int(a) for a in request.GET.getlist("anos") if a.isdigit() and int(a) in opcoes["anos"]]
    if not anos_sel:
        anos_sel = opcoes["anos"]
    meses_sel = [m for m in request.GET.getlist("meses") if m in opcoes["meses"]]
    clientes_sel = [v for v in request.GET.getlist("clientes") if v in opcoes["clientes"]]
    categorias_sel = [v for v in request.GET.getlist("categorias") if v in opcoes["categorias"]]
    produtos_sel = [v for v in request.GET.getlist("produtos") if v in opcoes["produtos"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    estados_sel = [v for v in request.GET.getlist("estados") if v in opcoes["estados"]]
    cc_sel = [v for v in request.GET.getlist("centros_custo") if v in opcoes["centros_custo"]]

    df_f = servicos.aplicar_filtros(
        df, anos=anos_sel, meses=meses_sel, clientes=clientes_sel, categorias=categorias_sel,
        produtos=produtos_sel, tamanhos=tamanhos_sel, estados=estados_sel, centros_custo=cc_sel,
    )
    if df_f.empty:
        return HttpResponse("Nenhum item encontrado para os filtros selecionados.", status=404)

    _partes = []
    if len(anos_sel) != len(opcoes["anos"]):
        _partes.append("Ano: " + ", ".join(str(a) for a in anos_sel))
    if meses_sel:
        _partes.append("Mês: " + ", ".join(servicos.mes_label(m) for m in meses_sel))
    if clientes_sel:
        _partes.append("Cliente: " + ", ".join(clientes_sel))
    if categorias_sel:
        _partes.append("Categoria: " + ", ".join(categorias_sel))
    if produtos_sel:
        _partes.append("Produto: " + ", ".join(produtos_sel))
    if tamanhos_sel:
        _partes.append("Tamanho: " + ", ".join(tamanhos_sel))
    if estados_sel:
        _partes.append("Estado: " + ", ".join(estados_sel))
    if cc_sel:
        _partes.append("Centro de Custo: " + ", ".join(cc_sel))
    filtros_texto = " · ".join(_partes)

    if len(anos_sel) == 1 and not meses_sel:
        periodo_label = str(anos_sel[0])
    elif meses_sel:
        periodo_label = ", ".join(servicos.mes_label(m) for m in sorted(meses_sel))
    else:
        periodo_label = ", ".join(str(a) for a in sorted(anos_sel))

    kpis_res = servicos.kpis(df_f)
    resumo_cli = servicos.resumo_por_cliente(df_f, kpis_res["total_valor"])
    kpis = [
        ("Valor total", relatorio_pdf._fmt_rs(kpis_res["total_valor"])),
        ("Total de peças", relatorio_pdf._fmt(kpis_res["total_pecas"])),
        ("Pedidos únicos", relatorio_pdf._fmt(kpis_res["n_pedidos"])),
        ("Clientes ativos", str(kpis_res["n_clientes"])),
        ("Ticket médio", relatorio_pdf._fmt_rs(kpis_res["ticket_medio"])),
        ("SKUs", relatorio_pdf._fmt(kpis_res["n_produtos"])),
    ]

    conteudo = relatorio_pdf.gerar_pdf_carteira(
        periodo_label=periodo_label,
        filtros=filtros_texto,
        kpis=kpis,
        pecas_categoria=servicos.pecas_por_categoria(df_f)["tabela"],
        por_tamanho=servicos.por_tamanho(df_f),
        top_produtos=servicos.top_produtos(df_f, limite=9999),
        por_estado=servicos.por_estado_valor(df_f),
        por_centro_custo=servicos.por_centro_custo(df_f),
        resumo_cliente=resumo_cli,
        abc=servicos.curva_abc(resumo_cli, kpis_res["total_valor"]),
    )
    nome = f"carteira-pedidos-{slugify(periodo_label)}.pdf"
    return _pdf_response(request, conteudo, nome)
