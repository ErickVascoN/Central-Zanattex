from django.apps import AppConfig


class CargasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cargas"
    verbose_name = "Previsão de Cargas"

    def ready(self):
        from . import sync
        sync.registrar()
