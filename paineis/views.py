from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from .modulos import MODULOS_POR_SLUG, modulos_por_aba


@login_required
def home(request):
    """Home com as abas de setor e os cards de cada módulo."""
    abas = modulos_por_aba()
    aba_inicial = next((aba for aba, mods in abas.items() if mods), "")
    contexto = {
        "abas": abas,
        "aba_inicial": aba_inicial,
        "titulo_pagina": "Central de Dados",
    }
    return render(request, "paineis/home.html", contexto)


@login_required
def modulo(request, slug):
    """Placeholder navegável de um módulo (dashboard entra nas próximas fases)."""
    mod = MODULOS_POR_SLUG.get(slug)
    if not mod:
        raise Http404("Módulo não encontrado")

    if mod.get("admin_only") and not request.user.is_superuser:
        return render(request, "paineis/sem_acesso.html", {"modulo": mod}, status=403)

    return render(request, "paineis/modulo.html", {"modulo": mod, "titulo_pagina": mod["nome"]})
