from django.apps import AppConfig


class CorteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "corte"
    verbose_name = "Controle de Corte"

    def ready(self):
        from . import sync
        sync.registrar()
