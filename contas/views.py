from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST


@require_POST
def heartbeat(request):
    """Chamado via JS a cada poucos minutos (ver templates/base.html)
    enquanto a aba/app fica aberta — qualquer request autenticado já renova
    o cookie de sessão sozinho (SESSION_SAVE_EVERY_REQUEST, settings.py);
    esse endpoint só existe pra gerar esse request quando o usuário está só
    lendo a tela, sem clicar em nada. Fechando a aba/app, os heartbeats
    param e o cookie (vida curta) expira sozinho em poucos minutos.

    Também devolve quanto falta pro teto absoluto de 2h (ver
    SessaoExpiradaMiddleware) — o JS usa isso pra avisar antes de expirar,
    em vez da pessoa só descobrir ao clicar em algo e cair no login do nada."""
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False}, status=401)

    restante = None
    login_em = request.session.get("login_em")
    if login_em:
        inicio = datetime.fromisoformat(login_em)
        decorrido = (timezone.now() - inicio).total_seconds()
        restante = max(0, int(settings.SESSAO_LIMITE_ABSOLUTO_SEGUNDOS - decorrido))

    return JsonResponse({"ok": True, "restante_segundos": restante})
