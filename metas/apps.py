from django.apps import AppConfig


class MetasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "metas"
    verbose_name = "Metas / Previsão de Custos"

    def ready(self):
        from . import sync
        sync.registrar()
