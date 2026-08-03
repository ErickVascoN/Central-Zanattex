"""Rate limit por IP — janela fixa de 1 minuto, guardada no cache padrão do
Django (em memória do próprio processo; não precisa de Redis porque o
gunicorn roda só 1 worker nesse app, ver entrypoint.sh — o contador não
precisaria ser compartilhado entre processos).

Protege a única máquina/processo de ficar sobrecarregada (ou o consumo de
banda de disparar) sob tráfego abusivo/automatizado — não é sobre limitar o
uso normal de quem já está logado navegando/filtrando dashboards."""
import time

from django.http import HttpResponse

LIMITE_REQUISICOES = 240  # por IP, por janela de 1 min (~4/s em média)
JANELA_SEGUNDOS = 60


def _ip_cliente(request) -> str:
    # Fly injeta o IP real do cliente nesse header — mais confiável do que
    # decompor X-Forwarded-For na mão atrás do proxy da Fly.
    return request.META.get("HTTP_FLY_CLIENT_IP") or request.META.get("REMOTE_ADDR", "desconhecido")


class RateLimitMiddleware:
    """Contador de janela fixa: cada minuto-relógio vira uma chave de cache
    própria (expira sozinha), evitando o problema de `cache.incr()` não
    resetar o timeout de uma chave já existente."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.core.cache import cache

        ip = _ip_cliente(request)
        janela = int(time.time() // JANELA_SEGUNDOS)
        chave = f"ratelimit:{ip}:{janela}"

        contagem = cache.get(chave, 0)
        if contagem >= LIMITE_REQUISICOES:
            return HttpResponse(
                "Muitas requisições em pouco tempo. Aguarde um minuto e tente de novo.",
                status=429, content_type="text/plain; charset=utf-8",
            )
        try:
            cache.incr(chave)
        except ValueError:
            # 1ª requisição dessa janela pra esse IP — cria a chave já com o
            # timeout; uma folga de 5s cobre o clock skew entre workers.
            cache.set(chave, 1, timeout=JANELA_SEGUNDOS + 5)

        return self.get_response(request)
