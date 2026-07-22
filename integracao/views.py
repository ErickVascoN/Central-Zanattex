from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.shortcuts import redirect, render

from . import sheets_client
from .fontes import todas_as_fontes


@staff_member_required
def status_fontes(request):
    """Tela (admin) que mostra o status da leitura ao vivo de cada planilha e o
    cache atual. Confirma que preencher a planilha alimenta os dashboards."""
    fontes = todas_as_fontes()
    cache = {c["arquivo"]: c for c in sheets_client.cache_status()}

    linhas = []
    for chave, f in fontes.items():
        nome_arq = f"{f['id']}_{f.get('gid', '0')}.csv".replace("/", "_")
        info = cache.get(nome_arq)
        linhas.append({
            "chave": chave,
            "label": f.get("label", chave),
            "ttl": f.get("ttl", 300),
            "em_cache": info is not None,
            "idade_min": info["idade_min"] if info else None,
            "tamanho_kb": info["tamanho_kb"] if info else None,
        })

    return render(request, "integracao/status_fontes.html", {
        "titulo_pagina": "Fonte de Dados",
        "linhas": linhas,
        "total": len(linhas),
        "em_cache": sum(1 for x in linhas if x["em_cache"]),
    })


@staff_member_required
def limpar_cache(request):
    """Limpa o cache — a próxima leitura busca tudo fresco do Google Sheets."""
    if request.method == "POST":
        n = sheets_client.invalidate_all()
        messages.success(request, f"Cache limpo: {n} arquivo(s) removido(s). Os dados serão rebaixados na próxima leitura.")
    return redirect("integracao:status_fontes")
