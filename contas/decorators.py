"""Bloqueia views inteiras para quem não é administrador (is_superuser) —
mesma régua do 'admin_only' dos módulos (paineis/modulos.py). Não esconde o
link/menu: mostra a tela de acesso restrito ao tentar abrir.

`setor_required` é a versão por setor (PCP/Corte/RH/...) — régua separada,
não mexe em admin_only. Ver contas/permissions.py pra regra completa de
quem fica sem restrição (só superuser)."""
from functools import wraps

from django.shortcuts import render

from .permissions import get_setor, usuario_sem_restricao


def admin_required(nome_area):
    """@admin_required("Carteira de Pedidos") — usa is_superuser (== ADM nesta
    Central). Quem não é admin recebe paineis/sem_acesso.html (status 403)."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_superuser:
                return render(request, "paineis/sem_acesso.html",
                              {"modulo": {"nome": nome_area}}, status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def setor_required(*setores, nome_area=""):
    """@setor_required("CORTE") — libera pra superuser e pra quem estiver
    num dos setores passados. Quem não se encaixa (inclusive quem não tem
    setor atribuído) recebe paineis/sem_acesso.html (status 403), mesmo
    padrão do admin_required."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if usuario_sem_restricao(request.user) or get_setor(request.user) in setores:
                return view_func(request, *args, **kwargs)
            return render(request, "paineis/sem_acesso.html",
                          {"modulo": {"nome": nome_area}}, status=403)
        return wrapper
    return decorator
