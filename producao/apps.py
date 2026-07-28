from django.apps import AppConfig


class ProducaoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'producao'

    def ready(self):
        from . import sync
        sync.registrar()
