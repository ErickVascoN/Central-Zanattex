from django.contrib import admin

from .models import RegraPremiacao


@admin.register(RegraPremiacao)
class RegraPremiacaoAdmin(admin.ModelAdmin):
    list_display = (
        "atividade", "unidade", "usa_tamanho", "meta_padrao",
        "valor_peca_excedente", "vigente_desde", "vigente_ate",
    )
    list_filter = ("unidade", "usa_tamanho")
    ordering = ("unidade", "atividade", "-vigente_desde")
    fieldsets = (
        (None, {"fields": ("unidade", "atividade")}),
        ("Meta por tamanho (Costura de Canto/Elástico)", {
            "fields": ("usa_tamanho", "meta_solteiro", "meta_casal",
                       "meta_queen", "meta_tamanhos_mistos"),
        }),
        ("Meta padrão (demais atividades)", {"fields": ("meta_padrao",)}),
        ("Premiação", {"fields": ("valor_peca_excedente",)}),
        ("Vigência", {"fields": ("vigente_desde", "vigente_ate")}),
    )
