"""Rate limit por IP — janela fixa de 1 minuto, guardada no cache padrão do
Django (em memória do próprio processo; não precisa de Redis porque o
gunicorn roda só 1 worker nesse app, ver entrypoint.sh — o contador não
precisaria ser compartilhado entre processos).

Protege a única máquina/processo de ficar sobrecarregada (ou o consumo de
banda de disparar) sob tráfego abusivo/automatizado. Dois patamares, não um
só: se o escritório sai pra internet por um único IP (comum em empresa
pequena/média com NAT), várias pessoas navegando ao mesmo tempo contam pro
MESMO IP — um limite único e apertado derrubaria gente de verdade. Por isso
quem já passou pela senha (autenticado) tem uma cota bem mais folgada; o
limite apertado fica só pra tráfego anônimo (tela de login, bots, scanners)."""
import time

from django.http import HttpResponse

# Por IP, por janela de 1 min. Números generosos de propósito — a ideia é
# barrar automação/abuso descarado, não usuário de verdade clicando rápido
# nem um escritório inteiro atrás do mesmo NAT.
LIMITE_ANONIMO = 300        # ~5/s — tela de login, tentativas, visitantes
LIMITE_AUTENTICADO = 1800   # ~30/s — várias pessoas logadas no mesmo IP
JANELA_SEGUNDOS = 60


def _ip_cliente(request) -> str:
    # Fly injeta o IP real do cliente nesse header — mais confiável do que
    # decompor X-Forwarded-For na mão atrás do proxy da Fly.
    return request.META.get("HTTP_FLY_CLIENT_IP") or request.META.get("REMOTE_ADDR", "desconhecido")


class RateLimitMiddleware:
    """Contador de janela fixa: cada minuto-relógio vira uma chave de cache
    própria (expira sozinha), evitando o problema de `cache.incr()` não
    resetar o timeout de uma chave já existente.

    Precisa rodar DEPOIS do AuthenticationMiddleware (ver MIDDLEWARE em
    settings.py) — é o `request.user` já resolvido que decide qual dos dois
    limites vale pra essa requisição."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.core.cache import cache

        autenticado = getattr(request.user, "is_authenticated", False)
        limite = LIMITE_AUTENTICADO if autenticado else LIMITE_ANONIMO
        tipo = "auth" if autenticado else "anon"

        ip = _ip_cliente(request)
        janela = int(time.time() // JANELA_SEGUNDOS)
        chave = f"ratelimit:{tipo}:{ip}:{janela}"

        contagem = cache.get(chave, 0)
        if contagem >= limite:
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
