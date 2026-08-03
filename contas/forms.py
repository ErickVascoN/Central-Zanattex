"""Form de login com limite de tentativas — checa o bloqueio ANTES de chamar
authenticate() (não gasta o hash de senha nem dá pista de timing enquanto
bloqueado). O registro da falha em si é feito pelo signal user_login_failed
(ver contas/signals.py), disparado dentro do clean() padrão do Django."""
import math

from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import TentativaLogin


class LoginComLimiteForm(AuthenticationForm):
    def clean(self):
        identificador = (self.cleaned_data.get("username") or "").strip().lower()
        if identificador:
            tentativa = TentativaLogin.objects.filter(identificador=identificador).first()
            if tentativa and tentativa.bloqueado_ate and tentativa.bloqueado_ate > timezone.now():
                minutos = math.ceil((tentativa.bloqueado_ate - timezone.now()).total_seconds() / 60)
                raise ValidationError(
                    "Muitas tentativas incorretas. Tente de novo em "
                    f"{minutos} minuto{'s' if minutos != 1 else ''}.",
                    code="bloqueado",
                )
        return super().clean()
