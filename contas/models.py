from django.conf import settings
from django.db import models


class UnidadeCorte(models.TextChoices):
    """As 5 fontes reais de corte hoje (corte/servicos.py, lencol_servicos.py,
    cortina_servicos.py, itaju_servicos.py) — mais granular que o `local` de
    ProgramacaoCorte (que agrupa só Giattex/Zanattex/Lençol pra fins de
    planejamento). Usado pra escopar RegistroCorte e o `unidade_corte` do
    PerfilUsuario de quem é do setor Corte."""
    AREALVA_MANTA = "AREALVA_MANTA", "Manta Arealva"
    IACANGA_MANTA = "IACANGA_MANTA", "Manta Iacanga"
    LENCOL = "LENCOL", "Lençol"
    CORTINA = "CORTINA", "Cortina"
    ITAJU = "ITAJU", "Itaju"


class Setor(models.TextChoices):
    """Lista aberta pra crescer — adicionar um setor novo é só uma linha
    aqui + marcar `setores` nos módulos que ele deve enxergar em
    paineis/modulos.py. Não precisa de migração de schema (é TextChoices,
    não uma tabela à parte). Independente do `is_superuser`/admin_only, que
    continua sendo a régua de dados financeiros/sensíveis dentro dos módulos
    já acessíveis — isso aqui só controla QUAIS módulos aparecem."""
    PCP = "PCP", "PCP"
    CORTE = "CORTE", "Corte"
    RH = "RH", "Recursos Humanos"
    COMERCIAL = "COMERCIAL", "Comercial"
    LOGISTICA = "LOGISTICA", "Logística"
    CONTROLADORIA = "CONTROLADORIA", "Controladoria"


class PerfilUsuario(models.Model):
    """Setor/unidade de um usuário — controla quais módulos aparecem na
    sidebar (ver paineis/context_processors.py e contas/middleware.py).
    Sem perfil, ou com `setor` em branco, o usuário não é restrito (mesmo
    comportamento de hoje, antes deste model existir) — só é restrito quem
    tiver um setor explicitamente atribuído. Superuser sempre vê tudo,
    independente do que estiver aqui."""
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    setor = models.CharField(
        "Setor", max_length=20, choices=Setor.choices, blank=True,
        help_text="Em branco = sem restrição extra de módulos (vê tudo, igual a hoje).")
    unidade_corte = models.CharField(
        "Unidade de corte", max_length=20, choices=UnidadeCorte.choices, blank=True,
        help_text="Só relevante quando o setor é Corte — escopa a fila de Gestão de Corte.")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil de usuário"
        verbose_name_plural = "Perfis de usuário"

    def __str__(self):
        return f"{self.usuario.get_username()} — {self.get_setor_display() or 'sem setor'}"


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
