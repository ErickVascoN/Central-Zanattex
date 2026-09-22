from __future__ import annotations

from django import forms


class _MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class _MultipleFileField(forms.FileField):
    """Padrão oficial do Django pra upload de múltiplos arquivos num só
    campo (o FileField padrão só aceita um) — ver docs "Uploading multiple
    files". `clean()` roda a validação de cada arquivo individualmente
    quando `data` vem como lista (POST com `multiple` no input)."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", _MultipleFileInput(attrs={"multiple": True, "accept": ".xml"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(d, initial) for d in data]
        return single_file_clean(data, initial)


class UploadXmlForm(forms.Form):
    """A lista de arquivos de verdade é lida de
    `request.FILES.getlist("arquivos")` na view (mais simples pra iterar) —
    este form só valida que os arquivos enviados são .xml antes disso."""
    arquivos = _MultipleFileField(label="Arquivos XML da NF-e")

    def clean_arquivos(self):
        arquivos = self.cleaned_data.get("arquivos") or []
        for arquivo in arquivos:
            if not arquivo.name.lower().endswith(".xml"):
                raise forms.ValidationError(f'"{arquivo.name}" não é um arquivo .xml.')
        return arquivos


class ResolverPendenciaForm(forms.Form):
    """Resolução manual de uma pendência de casamento: ou escolhe um item de
    entrada candidato, ou marca como 'sem correspondência' com uma
    justificativa (nunca resolve sem deixar rastro). `lembrar_associacao` é
    opt-in — só grava a correspondência de código de produto pra próxima
    vez se a pessoa marcar explicitamente (ver AssociacaoProduto)."""
    entrada_item_id = forms.IntegerField(required=False)
    ignorar_com_justificativa = forms.CharField(required=False, widget=forms.Textarea)
    lembrar_associacao = forms.BooleanField(required=False)

    def clean(self):
        dados = super().clean()
        if not dados.get("entrada_item_id") and not dados.get("ignorar_com_justificativa", "").strip():
            raise forms.ValidationError(
                "Escolha um item de entrada ou explique por que essa pendência deve ser ignorada.")
        return dados
