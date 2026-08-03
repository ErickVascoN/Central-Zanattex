"""Contagem de tentativas de login e bloqueio progressivo — ver TentativaLogin
em contas/models.py e o form em contas/forms.py (que checa o bloqueio antes
de autenticar)."""
from datetime import timedelta

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from .models import TentativaLogin

LIMITE_FALHAS = 3
BLOQUEIO_MAX_MINUTOS = 60


def _identificador(username: str) -> str:
    return (username or "").strip().lower()


@receiver(user_login_failed)
def registrar_falha_login(sender, credentials, **kwargs):
    identificador = _identificador(credentials.get("username", ""))
    if not identificador:
        return
    tentativa, _ = TentativaLogin.objects.get_or_create(identificador=identificador)
    tentativa.falhas += 1
    if tentativa.falhas >= LIMITE_FALHAS:
        tentativa.bloqueios += 1
        # dobra a cada bloqueio novo (1, 2, 4, 8... min), até o teto
        minutos = min(BLOQUEIO_MAX_MINUTOS, 2 ** (tentativa.bloqueios - 1))
        tentativa.bloqueado_ate = timezone.now() + timedelta(minutes=minutos)
        tentativa.falhas = 0
    tentativa.save()


@receiver(user_logged_in)
def resetar_tentativas_login(sender, user, **kwargs):
    TentativaLogin.objects.filter(identificador=_identificador(user.get_username())).delete()
