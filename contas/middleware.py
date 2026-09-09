"""Reforço global do filtro de setor — a sidebar (paineis/context_processors.py)
já esconde os módulos que o setor do usuário não deve ver, mas isso não
impede alguém de digitar a URL direto. Este middleware bloqueia isso pra
qualquer módulo listado em paineis/modulos.py::MODULOS que tenha `setores`
definido (mesma regra pra qualquer setor, não só Corte).

Só age em URLs que batem com o `url_name` de algum módulo — sub-URLs
"internas" de um app (ex.: endpoints de API/exportação que não têm entrada
própria em MODULOS) não são cobertas aqui; views sensíveis o suficiente pra
precisar disso devem usar @setor_required diretamente (contas/decorators.py).

Precisa rodar DEPOIS do AuthenticationMiddleware — usa request.user
já resolvido (ver MIDDLEWARE em central/settings.py)."""
from __future__ import annotations

from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from .permissions import modulo_liberado, usuario_sem_restricao


class SetorAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request.user, "is_authenticated", False) and not usuario_sem_restricao(request.user):
            from paineis.modulos import MODULOS

            # `request.resolver_match` só fica disponível DEPOIS que
            # get_response despacha pra view — nesse ponto do middleware
            # (antes de chamar get_response) ainda é None, então resolve a
            # URL na mão em vez de depender dele.
            view_name = None
            try:
                view_name = resolve(request.path_info).view_name
            except Resolver404:
                pass

            modulo_atual = (
                next((m for m in MODULOS if view_name and m.get("url_name") == view_name), None)
                if view_name else None
            )

            if modulo_atual is not None and not modulo_liberado(request.user, modulo_atual):
                destino = next((m for m in MODULOS if m.get("url_name") and modulo_liberado(request.user, m)), None)
                if destino is not None:
                    try:
                        return redirect(reverse(destino["url_name"]))
                    except NoReverseMatch:
                        pass
                return render(request, "paineis/sem_acesso.html",
                              {"modulo": {"nome": modulo_atual.get("nome", "")}}, status=403)

        return self.get_response(request)
