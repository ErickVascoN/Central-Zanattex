from django.contrib import admin

from .models import Cortador, EstacaoCorte


@admin.register(Cortador)
class CortadorAdmin(admin.ModelAdmin):
    list_display = ("nome", "unidade_corte", "ativo")
    list_filter = ("unidade_corte", "ativo")
    search_fields = ("nome",)


@admin.register(EstacaoCorte)
class EstacaoCorteAdmin(admin.ModelAdmin):
    list_display = ("nome", "unidade_corte", "ativo")
    list_filter = ("unidade_corte", "ativo")
    search_fields = ("nome",)
