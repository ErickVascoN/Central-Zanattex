from django.contrib import admin

from .models import FonteSincronizada


@admin.register(FonteSincronizada)
class FonteSincronizadaAdmin(admin.ModelAdmin):
    list_display = ("label", "status", "linhas", "ultima_sincronizacao", "tabela")
    list_filter = ("status",)
    ordering = ("label",)
    readonly_fields = (
        "chave", "tabela", "status", "linhas",
        "ultima_sincronizacao", "erro", "atualizado_em",
    )
