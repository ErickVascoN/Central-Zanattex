from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render

from . import sheets_client, sync_registry
from .db_sync import sync_dataframe
from .fontes import todas_as_fontes
from .models import FonteSincronizada


@staff_member_required
def status_fontes(request):
    """Tela (admin) com o que realmente alimenta os dashboards hoje: o status
    da sincronização Sheets -> Postgres (uma linha por fonte registrada em
    `sync_registry`) — mais o cache técnico de disco usado internamente pelo
    job de sync, como detalhe secundário de diagnóstico."""
    jobs = sync_registry.todos()
    status_por_chave = {f.chave: f for f in FonteSincronizada.objects.all()}

    sync_linhas = []
    for job in sorted(jobs, key=lambda j: j.label):
        f = status_por_chave.get(job.chave)
        sync_linhas.append({
            "label": job.label,
            "ttl": job.ttl_segundos,
            "status": f.status if f else "NUNCA",
            "linhas": f.linhas if f else None,
            "ultima_sincronizacao": f.ultima_sincronizacao if f else None,
            "erro": f.erro if f else "",
        })

    fontes = todas_as_fontes()
    cache = {c["arquivo"]: c for c in sheets_client.cache_status()}
    cache_linhas = []
    for chave, f in fontes.items():
        nome_arq = f"{f['id']}_{f.get('gid', '0')}.csv".replace("/", "_")
        info = cache.get(nome_arq)
        cache_linhas.append({
            "label": f.get("label", chave),
            "em_cache": info is not None,
            "idade_min": info["idade_min"] if info else None,
            "tamanho_kb": info["tamanho_kb"] if info else None,
        })

    return render(request, "integracao/status_fontes.html", {
        "titulo_pagina": "Fonte de Dados",
        "sync_linhas": sync_linhas,
        "sync_ok": sum(1 for x in sync_linhas if x["status"] == "OK"),
        "sync_total": len(sync_linhas),
        "cache_linhas": cache_linhas,
        "cache_total": len(cache_linhas),
        "cache_em_cache": sum(1 for x in cache_linhas if x["em_cache"]),
    })


@staff_member_required
def sincronizar_agora(request):
    """Roda a sincronização de todas as fontes registradas na hora, sem
    esperar o próximo ciclo do agendador em background."""
    if request.method == "POST":
        jobs = sync_registry.todos()
        ok = 0
        for job in jobs:
            df = job.carregar()
            if sync_dataframe(job.chave, job.tabela, df, label=job.label):
                ok += 1
        if ok == len(jobs):
            messages.success(request, f"Sincronização concluída: {ok}/{len(jobs)} fonte(s) atualizadas.")
        else:
            messages.warning(request, f"Sincronização concluída com falhas: {ok}/{len(jobs)} fonte(s) OK — veja os detalhes na tabela.")
    return redirect("integracao:status_fontes")


@staff_member_required
def limpar_cache(request):
    """Limpa o cache técnico do Sheets (detalhe interno do job de sync) — não
    afeta os dashboards diretamente, só força o próximo sync a baixar tudo
    fresco do Sheets em vez de reaproveitar o CSV em disco."""
    if request.method == "POST":
        n = sheets_client.invalidate_all()
        messages.success(request, f"Cache técnico limpo: {n} arquivo(s) removido(s).")
    return redirect("integracao:status_fontes")
