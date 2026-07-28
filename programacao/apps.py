from django.apps import AppConfig


class ProgramacaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "programacao"
    verbose_name = "Programação de Corte"

    def ready(self):
        from . import sync
        sync.registrar()
