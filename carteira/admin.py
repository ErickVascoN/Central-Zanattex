from django.contrib import admin

from .models import ImportacaoCarteira


@admin.register(ImportacaoCarteira)
class ImportacaoCarteiraAdmin(admin.ModelAdmin):
    list_display = ("nome_arquivo", "usuario", "linhas_importadas", "linhas_ignoradas", "criado_em")
    list_filter = ("criado_em",)
    search_fields = ("nome_arquivo", "usuario__username")
    readonly_fields = ("usuario", "nome_arquivo", "linhas_importadas", "linhas_ignoradas", "avisos", "criado_em")

    def has_add_permission(self, request):
        # Registro é criado só pela tela de importação (views.py), nunca à
        # mão — evita alguém criar uma linha de auditoria falsa pelo admin.
        return False
