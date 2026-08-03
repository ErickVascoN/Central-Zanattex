from django.db import models


class TentativaLogin(models.Model):
    """Controle de força bruta no login — 1 linha por usuário (identificador
    normalizado, minúsculo, casa com o CaseInsensitiveModelBackend). A cada 3
    senhas erradas seguidas bloqueia por um tempo que dobra a cada vez que o
    mesmo usuário volta a ser bloqueado (1min, 2min, 4min... até 60min),
    voltando a zero num login certo. Ver contas/signals.py e contas/forms.py."""
    identificador = models.CharField(max_length=150, unique=True)
    falhas = models.PositiveIntegerField(default=0)
    bloqueios = models.PositiveIntegerField(default=0)
    bloqueado_ate = models.DateTimeField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.identificador} ({self.falhas} falha(s), {self.bloqueios} bloqueio(s))"
