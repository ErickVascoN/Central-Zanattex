"""Views de infraestrutura do PWA — manifest, service worker e fallback
offline. Ficam aqui (não em `paineis`) por serem preocupação de app inteiro,
servidas na raiz do domínio, e precisam ser públicas (sem @login_required):
o navegador busca manifest.webmanifest e sw.js antes/independente do login,
inclusive na própria tela de login."""
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.cache import cache_control


@cache_control(max_age=3600)
def manifest(request):
    conteudo = render_to_string("pwa/manifest.webmanifest", request=request)
    return HttpResponse(conteudo, content_type="application/manifest+json")


@cache_control(no_cache=True)
def service_worker(request):
    """`no_cache` (não `no_store`): o navegador já revalida o SW sozinho a
    cada navegação: sem isso corre o risco de um proxy/CDN no meio do
    caminho servir uma versão antiga por mais tempo do que deveria."""
    conteudo = render_to_string("pwa/sw.js", request=request)
    resp = HttpResponse(conteudo, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    return resp


def offline(request):
    return render(request, "pwa/offline.html")
