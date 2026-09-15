from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    EnvioProducao, FechamentoOP, MetaPrestador, Prestador, RegistroProducao, RetornoProducao,
    opcoes_prestador,
)


@admin.register(EnvioProducao)
class EnvioProducaoAdmin(admin.ModelAdmin):
    list_display = ("programacao", "data", "tipo", "numero", "destino", "quantidade_pecas", "criado_por")
    list_filter = ("tipo", "data")
    search_fields = ("programacao__pedido", "programacao__cliente", "destino", "numero")


class PrestadorAdminForm(forms.ModelForm):
    """`nome` vira `<select>` travado na mesma lista de destino_costura —
    texto livre deixaria passar um nome quase-certo que nunca casaria com
    `EnvioProducao.destino` (ver Prestador.clean() e opcoes_prestador())."""

    class Meta:
        model = Prestador
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        opcoes = [(n, n) for n in opcoes_prestador()]
        # A opção atual pode ter saído da lista viva (facção desativada na
        # planilha) — mantém visível pra não travar a edição de um registro
        # antigo, só não some da lista sozinha.
        if self.instance.pk and self.instance.nome not in dict(opcoes):
            opcoes.append((self.instance.nome, f"{self.instance.nome} (fora da lista atual)"))
        self.fields["nome"].widget = forms.Select(
            choices=[("", "Selecione a facção…")] + sorted(opcoes))


class MetaPrestadorInline(admin.TabularInline):
    """Edita as metas do prestador na MESMA tela do cadastro — "quando
    formos cadastrar, já atribuir a meta" (pedido do usuário). `cliente`/
    `produto` continuam texto livre por ora (ninguém casa isso com outra
    fonte ainda — ver docstring de MetaPrestador)."""
    model = MetaPrestador
    extra = 1
    fields = ("produto", "cliente", "meta_pecas", "ativo")


@admin.register(Prestador)
class PrestadorAdmin(admin.ModelAdmin):
    """`nome` só aceita valores da lista canônica (ver PrestadorAdminForm) —
    sem isso o link nunca acharia nenhuma OP pra esse prestador. O link
    (coluna somente leitura) é o que se manda por WhatsApp; token nunca
    aparece editável (gerado sozinho, não deve ser trocado à mão)."""
    form = PrestadorAdminForm
    list_display = ("nome", "telefone", "ativo", "link_apontamento")
    list_filter = ("ativo",)
    search_fields = ("nome",)
    readonly_fields = ("token", "link_apontamento", "criado_em")
    inlines = [MetaPrestadorInline]

    @admin.display(description="Link de apontamento")
    def link_apontamento(self, obj):
        if not obj.pk:
            return "—"
        url = reverse("controle_op:prestador_lista", args=[obj.token])
        return format_html('<a href="{0}" target="_blank">{0}</a>', url)


@admin.register(RegistroProducao)
class RegistroProducaoAdmin(admin.ModelAdmin):
    list_display = ("programacao", "data", "destino", "quantidade_pecas", "qualidade_segunda_pecas",
                    "retalho_kg", "origem", "criado_por", "criado_por_nome")
    list_filter = ("data", "origem")
    search_fields = ("programacao__pedido", "programacao__cliente", "destino", "criado_por_nome")


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
