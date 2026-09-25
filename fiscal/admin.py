from django.contrib import admin

from .models import CentroCusto, Cliente, NotaFiscal, NotaFiscalItem, PendenciaMatching, Vinculo


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "ativo", "criado_em")
    search_fields = ("nome", "cnpj")
    list_filter = ("ativo",)


@admin.register(CentroCusto)
class CentroCustoAdmin(admin.ModelAdmin):
    """CNPJ próprio da Zanattex — não confundir com Cliente (dono do tecido).
    Desativar em vez de excluir se um dia parar de ser usado: nota antiga
    continua citando o CNPJ mesmo sem cadastro ativo (ver centro_custo em
    NotaFiscal, gravado direto do XML)."""
    list_display = ("nome", "cnpj", "ativo", "criado_em")
    search_fields = ("nome", "cnpj")
    list_filter = ("ativo",)


class NotaFiscalItemInline(admin.TabularInline):
    model = NotaFiscalItem
    extra = 0
    can_delete = False
    fields = ("n_item", "c_prod", "x_prod", "ncm", "cfop", "q_com", "saldo_atual", "tipo_retorno")
    readonly_fields = fields


@admin.register(NotaFiscal)
class NotaFiscalAdmin(admin.ModelAdmin):
    """Só inspeção/auditoria — dados importados não são editados manualmente
    aqui, o fluxo de correção é a fila de Pendências dentro do módulo."""
    list_display = ("n_nf", "tipo", "cliente", "status", "centro_custo", "data_emissao", "valor_total")
    list_filter = ("tipo", "status", "cliente", "centro_custo")
    search_fields = ("n_nf", "chave_acesso", "emit_nome", "dest_nome")
    date_hierarchy = "data_emissao"
    inlines = [NotaFiscalItemInline]
    readonly_fields = [f.name for f in NotaFiscal._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(PendenciaMatching)
class PendenciaMatchingAdmin(admin.ModelAdmin):
    list_display = ("motivo", "saida_item", "resolvido", "criado_em")
    list_filter = ("motivo", "resolvido")


@admin.register(Vinculo)
class VinculoAdmin(admin.ModelAdmin):
    list_display = ("saida_item", "entrada_item", "quantidade_baixada", "criado_em")
    search_fields = ("saida_item__x_prod", "entrada_item__x_prod")

    def has_add_permission(self, request):
        return False
