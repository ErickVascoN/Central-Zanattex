"""Bloqueia views inteiras para quem não é administrador (is_superuser) —
mesma régua do 'admin_only' dos módulos (paineis/modulos.py). Não esconde o
link/menu: mostra a tela de acesso restrito ao tentar abrir."""
from functools import wraps

from django.shortcuts import render


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
