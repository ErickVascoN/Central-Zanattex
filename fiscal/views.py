"""
Views do Saldo Fiscal. Toda view carrega @login_required + @setor_required —
o módulo é registrado em paineis/modulos.py com `url_externa` (abre em nova
aba, layout próprio, não herda o shell da Central — ver
fiscal/templates/fiscal/base_fiscal.html), então o SetorAccessMiddleware da
Central NÃO cobre essas URLs automaticamente. Cada view precisa se proteger
sozinha — é por isso que o decorator aparece em toda função aqui, mesmo
repetitivo, e não deve ser removido "pra simplificar"."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from contas.decorators import setor_required
from contas.models import Setor

from . import excel_export, importador, matching, relatorio_pdf, sefaz_servico, servicos
from .forms import ResolverPendenciaForm, UploadXmlForm
from .models import AssociacaoProduto, NotaFiscal, NotaFiscalItem, PendenciaMatching, Vinculo
from .nfe_xml import XmlInvalido

_fiscal = setor_required(Setor.FISCAL, nome_area="Saldo Fiscal")

_SESSAO_TOKEN = "fiscal_import_token"
_IDADE_MAX_UPLOAD_ORFAO_SEGUNDOS = 60 * 60
# Cartões por página na tela de revisão (fiscal/templates/fiscal/_partials/
# previa_revisao.html) — sem isso, um lote de milhares de XMLs virava um DOM
# gigante e travava o navegador (o resumo no topo continua somando o lote
# inteiro, só os cartões é que são paginados).
_ITENS_POR_PAGINA_REVISAO = 20
# Arquivos por requisição no envio e na confirmação em lotes (ver "Upload de
# XML" abaixo). O proxy do Fly derruba conexão que fica 60s sem trafegar
# dado. Envio (só parse + 2 queries) é barato: 250 levam <1s localmente. A
# confirmação grava nota por nota (commit + casamento) e chegou a 15s pra
# 250 localmente — por isso lote menor, com folga pra VM mais lenta.
_TAMANHO_LOTE = 250
_TAMANHO_LOTE_CONFIRMACAO = 100
_INDICE = "_indice.json"
_SESSAO_CONFIRMACAO = "fiscal_import_confirmacao"


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
    kpis = servicos.kpis_dashboard()
    return render(request, "fiscal/inicio.html", _contexto(
        "inicio", titulo_pagina="Saldo Fiscal", kpis=kpis,
        cards_totais=servicos.cards_totais(kpis.por_unidade),
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


# Fluxo em lotes (ver fiscal/templates/fiscal/_partials/upload_revisao.html):
# o navegador fatia a seleção e manda _TAMANHO_LOTE arquivos por vez pro
# endpoint de lote, que grava em disco e anota um resumo leve de cada
# arquivo num índice JSON da pasta (_INDICE). A revisão paginada soma o lote
# inteiro a partir desse índice e só reparseia os ~20 arquivos da página
# aberta; a confirmação também anda em lotes. Tudo isso porque o proxy do
# Fly derruba conexão que fica 60s sem trafegar dado — milhares de XMLs numa
# requisição só não terminam a tempo. Sem JS, o POST único de sempre
# continua funcionando (bom pra lote pequeno).
def _apagar_pasta(pasta: Path) -> None:
    shutil.rmtree(pasta, ignore_errors=True)


def _limpar_uploads_orfaos() -> None:
    base = Path(settings.BASE_DIR) / "cache" / "fiscal_uploads"
    if not base.exists():
        return
    agora = time.time()
    for pasta in base.iterdir():
        try:
            if pasta.is_dir() and agora - pasta.stat().st_mtime > _IDADE_MAX_UPLOAD_ORFAO_SEGUNDOS:
                _apagar_pasta(pasta)
        except OSError:
            pass  # outro request já está limpando a mesma pasta — sem problema


def _limpar_upload_da_sessao(request) -> None:
    token = request.session.pop(_SESSAO_TOKEN, None)
    request.session.pop(_SESSAO_CONFIRMACAO, None)
    if token:
        _apagar_pasta(Path(settings.BASE_DIR) / "cache" / "fiscal_uploads" / token)


def _ler_indice(pasta: Path) -> dict[str, dict]:
    try:
        return json.loads((pasta / _INDICE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _montar_previas(arquivos: list[tuple[str, bytes]], tipo_esperado: str) -> list[dict]:
    """Parse + prévia de cada arquivo com cliente/duplicata pré-carregados em
    2 queries pro conjunto inteiro (importador.prefetch_*), em vez de 2 por
    arquivo. Devolve no formato que previa_revisao.html espera.
    `tipo_esperado` só serve pra avisar se o arquivo parece ter sido enviado
    na tela errada; o tipo de verdade vem do próprio XML (CNPJ emit/dest)."""
    lidos = []
    for nome, conteudo in arquivos:
        try:
            lidos.append((nome, importador.parse_nfe(conteudo, nome), None))
        except XmlInvalido as e:
            lidos.append((nome, None, str(e)))
    lote = [parsed for _, parsed, _ in lidos if parsed is not None]
    clientes_cache = importador.prefetch_clientes(lote)
    chaves_importadas = importador.prefetch_chaves_importadas(lote)
    situacoes_sefaz = importador.prefetch_situacoes_sefaz(
        lote, clientes_cache=clientes_cache, chaves_importadas=chaves_importadas)

    previas = []
    for nome, parsed, erro in lidos:
        if parsed is not None:
            try:
                previa = importador.montar_previa_parsed(
                    parsed, nome, clientes_cache=clientes_cache, chaves_importadas=chaves_importadas,
                    situacoes_sefaz=situacoes_sefaz)
            except XmlInvalido as e:  # ex.: NF sem a Zanattex como emitente nem destinatária
                erro = str(e)
            else:
                previas.append({"nome_arquivo": nome, "previa": previa,
                                "tipo_diferente": previa.identificacao.tipo != tipo_esperado})
                continue
        previas.append({"nome_arquivo": nome, "erro": erro})
    return previas


def _gravar_e_indexar(pasta: Path, uploads, tipo_esperado: str) -> int:
    """Grava os XMLs recebidos na pasta do token e anota o resumo de cada um
    no índice (nome repetido substitui o anterior). Devolve quantos arquivos
    o lote tem no total, contando os de envios anteriores.

    Levanta ValueError se isso estourar FISCAL_MAX_ARQUIVOS_POR_ENVIO — teto
    sobre o TOTAL acumulado da sessão de upload (não por requisição: com JS,
    a seleção inteira já chega fatiada em POSTs de até `tamanho_lote`, ver
    _partials/upload_revisao.html). Checado antes de gravar qualquer arquivo
    ou consultar a SEFAZ (ver fiscal/importador.py::prefetch_situacoes_sefaz),
    pra falhar rápido sem desperdiçar nada."""
    indice_atual = _ler_indice(pasta)
    nomes_novos = {u.name for u in uploads if u.name.lower().endswith(".xml")}
    limite = settings.FISCAL_MAX_ARQUIVOS_POR_ENVIO
    total_projetado = len(set(indice_atual) | nomes_novos)
    if total_projetado > limite:
        raise ValueError(
            f"Permitido no máximo {limite} arquivos por sessão de importação — esta sessão já soma "
            f"{total_projetado}. Confirme o que já foi revisado antes de continuar, ou recomece com "
            "uma leva menor.")

    arquivos = []
    for upload in uploads:
        if not upload.name.lower().endswith(".xml"):
            continue  # também protege o _indice.json de ser sobrescrito
        conteudo = upload.read()
        (pasta / upload.name).write_bytes(conteudo)
        arquivos.append((upload.name, conteudo))

    indice = _ler_indice(pasta)
    for p in _montar_previas(arquivos, tipo_esperado):
        if "erro" in p:
            indice[p["nome_arquivo"]] = {"erro": p["erro"]}
            continue
        previa = p["previa"]
        por_unidade: dict[str, Decimal] = {}
        for item in previa.itens:
            por_unidade[item.unidade] = por_unidade.get(item.unidade, Decimal("0")) + item.quantidade
        indice[p["nome_arquivo"]] = {
            "pode_confirmar": previa.pode_confirmar,
            "ja_importada": previa.ja_importada,
            "valor_total": str(previa.parsed.valor_total),
            "por_unidade": {unidade: str(qtd) for unidade, qtd in por_unidade.items()},
        }
    (pasta / _INDICE).write_text(json.dumps(indice, ensure_ascii=False), encoding="utf-8")
    return len(indice)


def _resumo_do_indice(indice: dict[str, dict]) -> dict:
    """Sempre sobre o LOTE INTEIRO (não só a página aberta) — a paginação é
    só de renderização, não muda o que está sendo revisado."""
    por_unidade: dict[str, Decimal] = {}
    valor_total = Decimal("0")
    prontas = ja_cadastradas = invalidas = 0
    for entrada in indice.values():
        if "erro" in entrada:
            invalidas += 1
            continue
        prontas += entrada["pode_confirmar"]
        ja_cadastradas += entrada["ja_importada"]
        valor_total += Decimal(entrada["valor_total"])
        for unidade, qtd in entrada["por_unidade"].items():
            # Soma tudo que está na nota (KG, MT, o que for) — mesmo item
            # "Fora do escopo" (NCM ainda não controlado) entra aqui, esse
            # total é só informativo de quanto tá vindo na leva, não é o
            # mesmo escopo do saldo/baixa automática.
            por_unidade[unidade] = por_unidade.get(unidade, Decimal("0")) + Decimal(qtd)
    return {
        "prontas": prontas, "ja_cadastradas": ja_cadastradas, "invalidas": invalidas,
        "por_unidade": por_unidade, "valor_total": valor_total, "total_arquivos": len(indice),
    }


def _contexto_revisao(request, token: str, tipo_esperado: str, url_confirmar: str) -> dict:
    pasta = _dir_uploads(token)
    indice = _ler_indice(pasta)
    paginador = Paginator(list(indice), _ITENS_POR_PAGINA_REVISAO)
    pagina = paginador.get_page(request.GET.get("pagina") or request.POST.get("pagina") or 1)

    # Só os arquivos da página aberta são reparseados, pra montar os cartões
    # com o detalhe item a item — o resumo do topo vem do índice.
    por_nome = {}
    presentes = []
    for nome in pagina.object_list:
        caminho = pasta / nome
        if caminho.exists():
            presentes.append((nome, caminho.read_bytes()))
        else:
            por_nome[nome] = {"nome_arquivo": nome, "erro": "Arquivo não está mais disponível — envie de novo."}
    for p in _montar_previas(presentes, tipo_esperado):
        por_nome[p["nome_arquivo"]] = p

    return {
        "previas": [por_nome[nome] for nome in pagina.object_list],
        "pagina": pagina,
        # Django template não deixa chamar método com kwargs no {% for %}
        # (get_elided_page_range(on_each_side=..., on_ends=...)) — resolvido
        # aqui em vez de no template.
        "paginas_elided": list(paginador.get_elided_page_range(pagina.number, on_each_side=1, on_ends=1)),
        "token": token, "url_confirmar": url_confirmar, "resumo": _resumo_do_indice(indice),
    }


def _etapa1_upload(request, *, secao: str, tipo_esperado: str, template: str, titulo: str,
                    url_confirmar: str, url_lote: str, url_revisao: str):
    """Passo 1 do upload: GET mostra o form; POST (só sem JS — com JS o
    navegador usa _receber_lote + _revisao) grava tudo numa requisição só e
    mostra a prévia. Nada é gravado no banco nessa etapa."""
    contexto_upload = {
        "form": UploadXmlForm(), "url_lote": reverse(url_lote), "url_revisao": reverse(url_revisao),
        "tamanho_lote": _TAMANHO_LOTE, "max_arquivos_envio": settings.FISCAL_MAX_ARQUIVOS_POR_ENVIO,
    }
    if request.method != "POST":
        return render(request, template, _contexto(secao, titulo_pagina=titulo, **contexto_upload))

    arquivos = request.FILES.getlist("arquivos")
    if not arquivos:
        messages.error(request, "Selecione pelo menos um arquivo XML.")
        return render(request, template, _contexto(secao, titulo_pagina=titulo, **contexto_upload))

    _limpar_uploads_orfaos()
    _limpar_upload_da_sessao(request)
    token = uuid.uuid4().hex
    request.session[_SESSAO_TOKEN] = token
    try:
        _gravar_e_indexar(_dir_uploads(token), arquivos, tipo_esperado)
    except ValueError as e:
        del request.session[_SESSAO_TOKEN]
        messages.error(request, str(e))
        return render(request, template, _contexto(secao, titulo_pagina=titulo, **contexto_upload))
    return render(request, template, _contexto(
        secao, titulo_pagina=titulo, revisao=True, **contexto_upload,
        **_contexto_revisao(request, token, tipo_esperado, url_confirmar),
    ))


def _receber_lote(request, *, tipo_esperado: str):
    """POST com até _TAMANHO_LOTE arquivos (o navegador fatia a seleção).
    Sem `token`, começa um lote novo (descarta o anterior da sessão); com
    `token`, soma ao lote em andamento. Responde JSON com o total recebido."""
    if request.method != "POST":
        return JsonResponse({"erro": "Método não permitido."}, status=405)
    token = request.POST.get("token", "")
    if token:
        if token != request.session.get(_SESSAO_TOKEN):
            return JsonResponse(
                {"erro": "Sessão de importação expirada — selecione os arquivos de novo."}, status=409)
    else:
        _limpar_uploads_orfaos()
        _limpar_upload_da_sessao(request)
        token = uuid.uuid4().hex
        request.session[_SESSAO_TOKEN] = token
    try:
        total = _gravar_e_indexar(_dir_uploads(token), request.FILES.getlist("arquivos"), tipo_esperado)
    except ValueError as e:
        return JsonResponse({"erro": str(e)}, status=400)
    return JsonResponse({"token": token, "total": total})


def _revisao(request, *, tipo_esperado: str, url_confirmar: str):
    """Fragmento da revisão (uma página), buscado pelo navegador depois de
    cada envio e a cada troca de página — sem reenviar arquivo nenhum."""
    token = request.session.get(_SESSAO_TOKEN)
    if not token:
        return render(request, "fiscal/_partials/previa_revisao.html", {
            "previas": [], "pagina": Paginator([], _ITENS_POR_PAGINA_REVISAO).get_page(1),
            "paginas_elided": [], "token": "", "url_confirmar": url_confirmar,
            "resumo": _resumo_do_indice({}),
        })
    return render(request, "fiscal/_partials/previa_revisao.html",
                  _contexto_revisao(request, token, tipo_esperado, url_confirmar))


def _confirmar_arquivos(caminhos: list[Path], usuario) -> dict:
    """Grava de verdade, nota por nota (cada uma na própria transação — uma
    nota ruim não derruba as outras), com cliente/duplicata pré-carregados em
    2 queries pro conjunto inteiro. Devolve os totais num formato que cabe na
    sessão (JSON), pra somar entre os lotes."""
    lidos = []
    for caminho in caminhos:
        try:
            lidos.append((caminho.name, importador.parse_nfe(caminho.read_bytes(), caminho.name), None))
        except XmlInvalido as e:
            lidos.append((caminho.name, None, str(e)))
    lote = [parsed for _, parsed, _ in lidos if parsed is not None]
    clientes_cache = importador.prefetch_clientes(lote)
    chaves_importadas = importador.prefetch_chaves_importadas(lote)
    situacoes_sefaz = importador.prefetch_situacoes_sefaz(
        lote, clientes_cache=clientes_cache, chaves_importadas=chaves_importadas)

    totais = {"importadas": 0, "duplicadas": 0, "pendentes_cliente": [], "erros": []}
    for nome, parsed, erro in lidos:
        if parsed is not None:
            try:
                resultado = importador.confirmar_importacao_parsed(
                    parsed, nome, usuario=usuario,
                    clientes_cache=clientes_cache, chaves_importadas=chaves_importadas,
                    situacoes_sefaz=situacoes_sefaz)
            except XmlInvalido as e:  # ex.: NF sem a Zanattex como emitente nem destinatária
                erro = str(e)
            else:
                if resultado.status == "importada":
                    totais["importadas"] += 1
                elif resultado.status == "duplicada":
                    totais["duplicadas"] += 1
                elif resultado.status == "cliente_pendente":
                    totais["pendentes_cliente"].append(
                        f"{resultado.nome_cliente} (CNPJ {resultado.cnpj_cliente})")
                continue
        totais["erros"].append(f"{nome}: {erro}")
    return totais


def _mensagens_confirmacao(request, totais: dict) -> None:
    importadas, duplicadas = totais["importadas"], totais["duplicadas"]
    pendentes_cliente = sorted(set(totais["pendentes_cliente"]))
    erros = totais["erros"]
    if importadas:
        messages.success(request, f"{importadas} nota(s) importada(s) com sucesso.")
    if duplicadas:
        messages.info(request, f"{duplicadas} nota(s) já tinham sido importadas antes — ignoradas.")
    if pendentes_cliente:
        messages.warning(
            request,
            "CNPJ sem cliente cadastrado, nada foi importado desses arquivos: "
            + "; ".join(pendentes_cliente)
            + ". Cadastre o cliente no admin e envie de novo.")
    if erros:
        # Lote grande pode ter centenas de erros — lista inteira não cabe na tela.
        mostrados = "; ".join(erros[:20])
        resto = f" … e mais {len(erros) - 20} arquivo(s)." if len(erros) > 20 else ""
        messages.error(request, f"Falha ao processar {len(erros)} arquivo(s): {mostrados}{resto}")
    if not (importadas or duplicadas or pendentes_cliente or erros):
        messages.info(request, "Nada foi importado.")


def _etapa2_confirmar(request, *, url_voltar: str):
    """Passo 2 (só POST): reprocessa os arquivos staged em disco e grava de
    verdade. Idempotente — arquivo já importado antes vira 'duplicada' no
    resumo, não erro. Com `lote_inicio` no POST (navegador com JS) processa
    só _TAMANHO_LOTE_CONFIRMACAO arquivos a partir dali e responde JSON; no último lote
    monta as mensagens e devolve pra onde redirecionar. Sem `lote_inicio`,
    processa tudo numa requisição só (caminho sem JS)."""
    if request.method != "POST":
        return redirect(url_voltar)
    em_lotes = "lote_inicio" in request.POST

    token_sessao = request.session.get(_SESSAO_TOKEN)
    if not token_sessao or token_sessao != request.POST.get("token"):
        erro = "Sessão de importação expirada ou inválida — envie os arquivos de novo."
        if em_lotes:
            return JsonResponse({"erro": erro}, status=409)
        messages.error(request, erro)
        return redirect(url_voltar)

    if request.POST.get("acao") == "cancelar":
        _limpar_upload_da_sessao(request)
        messages.info(request, "Importação cancelada — nenhum dado foi alterado.")
        return redirect(url_voltar)

    arquivos = sorted(_dir_uploads(token_sessao).glob("*.xml"))
    if not arquivos:
        _limpar_upload_da_sessao(request)
        erro = "Os arquivos enviados não estão mais disponíveis — envie de novo."
        if em_lotes:
            return JsonResponse({"erro": erro}, status=410)
        messages.error(request, erro)
        return redirect(url_voltar)

    if not em_lotes:
        totais = _confirmar_arquivos(arquivos, request.user)
        _limpar_upload_da_sessao(request)
        _mensagens_confirmacao(request, totais)
        return redirect(url_voltar)

    try:
        inicio = max(0, int(request.POST["lote_inicio"]))
    except ValueError:
        return JsonResponse({"erro": "Lote inválido."}, status=400)
    parciais = _confirmar_arquivos(arquivos[inicio:inicio + _TAMANHO_LOTE_CONFIRMACAO], request.user)
    # Recomeçar do zero (ex.: nova tentativa depois de uma falha no meio)
    # zera a soma — as notas já gravadas antes voltam como "duplicadas".
    totais = request.session.get(_SESSAO_CONFIRMACAO) if inicio else None
    if totais is None:
        totais = parciais
    else:
        for chave in ("importadas", "duplicadas"):
            totais[chave] += parciais[chave]
        for chave in ("pendentes_cliente", "erros"):
            totais[chave].extend(parciais[chave])
    fim = min(inicio + _TAMANHO_LOTE_CONFIRMACAO, len(arquivos))

    if fim < len(arquivos):
        request.session[_SESSAO_CONFIRMACAO] = totais
        return JsonResponse({"processados": fim, "total": len(arquivos)})
    _limpar_upload_da_sessao(request)
    _mensagens_confirmacao(request, totais)
    return JsonResponse({"processados": fim, "total": len(arquivos), "redirecionar": reverse(url_voltar)})


@login_required
@_fiscal
def importar_entrada(request):
    return _etapa1_upload(
        request, secao="importar_entrada", tipo_esperado=NotaFiscal.Tipo.ENTRADA,
        template="fiscal/importar_entrada.html", titulo="Importar NF de Entrada",
        url_confirmar="fiscal:confirmar_importacao_entrada",
        url_lote="fiscal:lote_importacao_entrada", url_revisao="fiscal:revisao_importacao_entrada")


@login_required
@_fiscal
def lote_importacao_entrada(request):
    return _receber_lote(request, tipo_esperado=NotaFiscal.Tipo.ENTRADA)


@login_required
@_fiscal
def revisao_importacao_entrada(request):
    return _revisao(request, tipo_esperado=NotaFiscal.Tipo.ENTRADA,
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
        url_confirmar="fiscal:confirmar_importacao_saida",
        url_lote="fiscal:lote_importacao_saida", url_revisao="fiscal:revisao_importacao_saida")


@login_required
@_fiscal
def lote_importacao_saida(request):
    return _receber_lote(request, tipo_esperado=NotaFiscal.Tipo.SAIDA)


@login_required
@_fiscal
def revisao_importacao_saida(request):
    return _revisao(request, tipo_esperado=NotaFiscal.Tipo.SAIDA,
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
        situacao_choices=servicos.SITUACAO_CHOICES_FILTRO,
    ))


@login_required
@_fiscal
def relatorio_saldo_pdf(request):
    filtros = servicos.filtros_da_query(request.GET)
    itens = _itens_relatorio(filtros)
    # KPIs do MESMO recorte filtrado da tabela, por unidade (KG e MT nunca
    # somados) — antes eram os totais gerais do Início, ignorando o filtro.
    kpis = []
    for t in servicos.totais_por_unidade(servicos.saldo_por_produto(filtros)):
        kpis += [
            (f"Recebido ({t.unidade})", relatorio_pdf.fmt_br(t.recebido)),
            (f"Saldo ({t.unidade})", relatorio_pdf.fmt_br(t.saldo)),
            (f"Excedido ({t.unidade})", relatorio_pdf.fmt_br(t.excedido)),
        ]
    periodo = f"{filtros.data_inicio or '—'} a {filtros.data_fim or '—'}"
    conteudo = relatorio_pdf.gerar_pdf_saldo(
        periodo_label=periodo, filtros=servicos.rotulo_centro_custo(filtros.centro_custo) or "Todos os centros de custo",
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
        periodo_label=periodo, filtros=servicos.rotulo_centro_custo(filtros.centro_custo) or "Todos os centros de custo",
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
    """Os 4 cards do Histórico (tela e PDF): Entrada, Utilizado, Saldo e
    Excedência — cada um com a quantidade por unidade (KG e MT nunca
    somados) e o valor em R$ (esse pode somar). Entrada − Utilizado +
    Excedência = Saldo."""
    por_unidade = servicos.totais_por_unidade(l.item for l in itens)
    valores = {
        "recebido": sum((l.valor_entrada for l in itens), Decimal("0")),
        "utilizado": sum((l.valor_utilizado for l in itens), Decimal("0")),
        "saldo": sum((l.valor_saldo for l in itens), Decimal("0")),
        "excedido": sum((l.excedido * l.item.v_un_com for l in itens), Decimal("0")),
    }
    return {"cards": servicos.cards_totais(por_unidade, valores)}


@login_required
@_fiscal
def historico(request):
    filtros = servicos.filtros_da_query(request.GET)
    modo = request.GET.get("modo", "entrada")

    if modo == "saida":
        mostrar_insumos = request.GET.get("insumos") == "1"
        return render(request, "fiscal/historico.html", _contexto(
            "historico", titulo_pagina="Histórico", modo=modo, filtros=filtros,
            itens_saida=servicos.historico_saida_itens(filtros, mostrar_insumos=mostrar_insumos),
            opcoes_centro_custo=servicos.opcoes_centro_custo(),
            situacao_choices=servicos.SITUACAO_CHOICES_FILTRO,
            mostrar_insumos=mostrar_insumos,
        ))

    status_filtro = request.GET.get("status", "")
    todos_itens = servicos.historico_itens(filtros)
    # Chips de progresso de consumo só fazem sentido pra NF VALIDA (fora
    # disso, todo item cai num status uniforme tipo "Cancelada" — quem quer
    # ver isso já usa o filtro de Situação acima, não precisa dos dois ao
    # mesmo tempo, ver servicos.rotulo_status_historico).
    contagem_status: dict[str, int] = {}
    for linha in todos_itens:
        contagem_status[linha.status] = contagem_status.get(linha.status, 0) + 1
    itens = [l for l in todos_itens if l.status == status_filtro] if status_filtro else todos_itens
    chips_status = [
        (chave, rotulo, contagem_status.get(chave, 0)) for chave, rotulo in servicos.STATUS_HISTORICO.items()
    ] if not filtros.situacao else []
    return render(request, "fiscal/historico.html", _contexto(
        "historico", titulo_pagina="Histórico", modo=modo, itens=itens, filtros=filtros,
        status_filtro=status_filtro, chips_status=chips_status, total_itens=len(todos_itens),
        totais=_totais_historico(itens), opcoes_centro_custo=servicos.opcoes_centro_custo(),
        situacao_choices=servicos.SITUACAO_CHOICES_FILTRO,
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
        entrada_item, servicos.tem_pendencia_aberta(entrada_item))
    vinculos_com_valor = [(v, v.quantidade_baixada * entrada_item.v_un_com) for v in vinculos]
    return render(request, "fiscal/_partials/historico_expander.html", {
        "item": entrada_item, "vinculos": vinculos, "vinculos_com_valor": vinculos_com_valor,
        "saldo": saldo, "excedido": excedido,
        "utilizado": utilizado, "pct_utilizado": pct_utilizado, "pct_disponivel": pct_disponivel,
        "chave_formatada": chave_formatada, "status": status,
        "valor_recebido": entrada_item.q_com * entrada_item.v_un_com,
        "valor_utilizado": utilizado * entrada_item.v_un_com,
        "valor_saldo": saldo * entrada_item.v_un_com,
        "pendencias": servicos.pendencias_da_entrada(entrada_item),
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
        "chave": chave.rpartition("|")[0] or chave, "itens": itens, "total_recebido": total_recebido, "total_saldo": total_saldo,
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
def verificar_cancelamentos_sefaz_view(request):
    """Botão manual da tela Pendências — mesma checagem do cron
    (fiscal/sefaz_servico.py), só que síncrona e disparada por clique."""
    if request.method != "POST":
        return redirect("fiscal:pendencias")
    resumo = sefaz_servico.verificar_cancelamentos()
    messages.success(
        request,
        f"Verificadas {resumo.verificadas} nota(s) — {resumo.novas_para_revisao} nova(s) pra revisão, "
        f"{resumo.erros} sem resposta (tentam de novo na próxima checagem).")
    return redirect(f"{reverse('fiscal:pendencias')}?motivo=CANCELADA_SEFAZ")


@login_required
@_fiscal
def resolver_pendencia(request, pendencia_id: int):
    pendencia = get_object_or_404(PendenciaMatching, pk=pendencia_id, resolvido=False)
    eh_duplicidade = pendencia.motivo == PendenciaMatching.Motivo.POSSIVEL_DUPLICIDADE
    eh_cancelamento_sefaz = pendencia.motivo == PendenciaMatching.Motivo.CANCELADA_SEFAZ
    notas_duplicadas = []
    if eh_duplicidade and pendencia.saida_item:
        nota = pendencia.saida_item.nota_fiscal
        notas_duplicadas = sorted([nota, *matching.duplicatas_de(nota)], key=lambda n: n.data_emissao)

    if request.method != "POST":
        candidatos = []
        vinculos_nota_cancelada = []
        if eh_cancelamento_sefaz and pendencia.nota_fiscal:
            # Impacto: toda baixa que essa nota já aplicou (SAIDA) ou já
            # recebeu (ENTRADA) — pra quem for decidir ver o que reverte se
            # excluir da carteira.
            nota_cs = pendencia.nota_fiscal
            if nota_cs.tipo == NotaFiscal.Tipo.SAIDA:
                vinculos_nota_cancelada = list(
                    Vinculo.objects.filter(saida_item__nota_fiscal=nota_cs)
                    .select_related("saida_item", "entrada_item__nota_fiscal"))
            else:
                vinculos_nota_cancelada = list(
                    Vinculo.objects.filter(entrada_item__nota_fiscal=nota_cs)
                    .select_related("saida_item__nota_fiscal", "entrada_item"))
        if pendencia.saida_item and not eh_duplicidade:
            # Primeiro os itens que o casamento já apontou (candidatos
            # empatados, item excedido...), depois o resto do tecido do
            # cliente — inclusive item já zerado: o certo pode ter acabado.
            afetados = list(pendencia.itens_entrada.select_related("nota_fiscal"))
            outros = (NotaFiscalItem.objects.filter(
                nota_fiscal__cliente=pendencia.saida_item.nota_fiscal.cliente,
                nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA,
                nota_fiscal__situacao=NotaFiscal.Situacao.VALIDA, saldo_atual__isnull=False,
            ).exclude(pk__in=[i.pk for i in afetados])
                .select_related("nota_fiscal").order_by("-nota_fiscal__data_emissao")[:50])
            candidatos = afetados + list(outros)
        return render(request, "fiscal/resolver_pendencia.html", _contexto(
            "pendencias", titulo_pagina="Resolver pendência",
            pendencia=pendencia, candidatos=candidatos, form=ResolverPendenciaForm(),
            notas_duplicadas=notas_duplicadas,
            # Só os itens que ESTA pendência já aponta como afetados ganham a
            # ação "excluir do controle de saldo" no template — os "outros"
            # do resto da lista são só sugestões genéricas de correspondência,
            # excluí-los não faz sentido nesse fluxo.
            afetados_ids={i.pk for i in afetados} if pendencia.saida_item and not eh_duplicidade else set(),
            baixa_atual=list(pendencia.saida_item.vinculos_saida.select_related("entrada_item__nota_fiscal"))
            if pendencia.saida_item else [],
            vinculos_nota_cancelada=vinculos_nota_cancelada,
        ))

    form = ResolverPendenciaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escolha um item de entrada ou informe a justificativa.")
        return redirect("fiscal:pendencias")

    if eh_cancelamento_sefaz and pendencia.nota_fiscal:
        nota_cs = pendencia.nota_fiscal
        agora = timezone.now()
        if form.cleaned_data.get("confirmar_cancelamento"):
            matching.marcar_cancelada(nota_cs, request.user)
            mensagem = f"NF {nota_cs.n_nf} marcada como cancelada — as baixas dela foram desfeitas."
        elif form.cleaned_data.get("ignorar_com_justificativa", "").strip():
            mensagem = "Mantida no saldo. Justificativa: " + form.cleaned_data["ignorar_com_justificativa"]
        else:
            messages.error(request, "Escolha excluir da carteira ou justifique por que a NF deve ser mantida.")
            return redirect("fiscal:pendencias")
        NotaFiscal.objects.filter(pk=nota_cs.pk).update(
            cancelamento_revisao_pendente=False, cancelamento_revisado_por=request.user,
            cancelamento_revisado_em=agora)
        pendencia.resolvido = True
        pendencia.resolvido_por = request.user
        pendencia.resolvido_em = agora
        pendencia.detalhe += f"\n\nResolução: {mensagem}"
        pendencia.save(update_fields=["resolvido", "resolvido_por", "resolvido_em", "detalhe"])
        messages.success(request, mensagem)
        return redirect("fiscal:pendencias")

    cancelar_id = form.cleaned_data.get("cancelar_nota_id")
    if cancelar_id:
        nota = next((n for n in notas_duplicadas if n.pk == cancelar_id), None)
        if nota is None:
            messages.error(request, "Essa NF não faz parte da duplicidade desta pendência.")
            return redirect("fiscal:pendencias")
        # Desfaz as baixas da nota cancelada e resolve as pendências de
        # duplicidade ligadas a ela (inclusive esta).
        matching.marcar_cancelada(nota, request.user)
        messages.success(request, f"NF {nota.n_nf} marcada como cancelada — as baixas dela foram desfeitas.")
        return redirect("fiscal:pendencias")

    excluir_nota_id = form.cleaned_data.get("excluir_nota_id")
    if excluir_nota_id:
        nota = next((n for n in notas_duplicadas if n.pk == excluir_nota_id), None)
        if nota is None:
            messages.error(request, "Essa NF não faz parte da duplicidade desta pendência.")
            return redirect("fiscal:pendencias")
        matching.excluir_nota_do_saldo(nota, request.user)
        messages.success(
            request, f"NF {nota.n_nf} excluída do controle de saldo — as baixas dela foram desfeitas.")
        return redirect("fiscal:pendencias")

    excluir_entrada_item_id = form.cleaned_data.get("excluir_entrada_item_id")
    if excluir_entrada_item_id:
        entrada_item = get_object_or_404(
            NotaFiscalItem, pk=excluir_entrada_item_id, nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA)
        matching.excluir_item_do_saldo(entrada_item, request.user)
        mensagem = (
            f"Item #{entrada_item.n_item} da NF {entrada_item.nota_fiscal.n_nf} excluído do controle "
            "de saldo — não afirma cancelamento na SEFAZ, só para de contar.")
        pendencia.resolvido = True
        pendencia.resolvido_por = request.user
        pendencia.detalhe += f"\n\nResolução: {mensagem}"
        pendencia.resolvido_em = timezone.now()
        pendencia.save(update_fields=["resolvido", "resolvido_por", "resolvido_em", "detalhe"])
        matching.atualizar_status(pendencia.saida_item.nota_fiscal)
        messages.success(request, mensagem)
        return redirect("fiscal:pendencias")

    entrada_item_id = form.cleaned_data.get("entrada_item_id")
    if entrada_item_id:
        entrada_item = get_object_or_404(
            NotaFiscalItem, pk=entrada_item_id, nota_fiscal__tipo=NotaFiscal.Tipo.ENTRADA)
        # A escolha manual SUBSTITUI a baixa automática desse item (no
        # excesso ela já tinha sido aplicada) — senão o mesmo tecido
        # devolvido seria baixado duas vezes.
        for v in pendencia.saida_item.vinculos_saida.all():
            NotaFiscalItem.objects.filter(pk=v.entrada_item_id).update(
                saldo_atual=F("saldo_atual") + v.quantidade_baixada)
            v.delete()
        entrada_item.refresh_from_db()
        aviso = matching.aplicar_baixa(pendencia.saida_item, entrada_item)
        mensagem = f"Baixa aplicada manualmente contra a NF {entrada_item.nota_fiscal.n_nf}."
        if aviso:
            mensagem += f" Atenção: {aviso}"
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
    if pendencia.saida_item:
        # Outras pendências abertas do mesmo item (ex.: o excesso da baixa
        # substituída) perdem o sentido junto.
        if entrada_item_id:
            PendenciaMatching.objects.filter(
                saida_item=pendencia.saida_item, resolvido=False,
                motivo=PendenciaMatching.Motivo.QUANTIDADE_EXCEDIDA).delete()
        matching.atualizar_status(pendencia.saida_item.nota_fiscal)

    messages.success(request, "Pendência resolvida.")
    return redirect("fiscal:pendencias")
