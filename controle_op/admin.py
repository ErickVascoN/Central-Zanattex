from django.contrib import admin

from .models import EnvioProducao, FechamentoOP, RetornoProducao


@admin.register(EnvioProducao)
class EnvioProducaoAdmin(admin.ModelAdmin):
    list_display = ("programacao", "data", "destino", "quantidade_pecas", "criado_por")
    list_filter = ("data",)
    search_fields = ("programacao__pedido", "programacao__cliente", "destino")


@admin.register(RetornoProducao)
class RetornoProducaoAdmin(admin.ModelAdmin):
    list_display = ("programacao", "data", "quantidade_pecas", "retalho_kg", "criado_por")
    list_filter = ("data",)
    search_fields = ("programacao__pedido", "programacao__cliente")


@admin.register(FechamentoOP)
class FechamentoOPAdmin(admin.ModelAdmin):
    list_display = ("programacao", "faturamento_confirmado", "faturamento_confirmado_em", "faturamento_confirmado_por")
    list_filter = ("faturamento_confirmado",)
    search_fields = ("programacao__pedido", "programacao__cliente")
