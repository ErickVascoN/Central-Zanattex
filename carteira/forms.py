from __future__ import annotations

from django import forms

_EXTENSOES_ACEITAS = (".xlsx", ".xls")


class ImportarExcelForm(forms.Form):
    arquivo = forms.FileField(
        label="Arquivo Excel da Carteira de Pedidos",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.xls", "class": "field-input"}))

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        nome = arquivo.name.lower()
        if not nome.endswith(_EXTENSOES_ACEITAS):
            raise forms.ValidationError(
                "Envie um arquivo Excel (.xlsx ou .xls) — o mesmo formato exportado da Carteira de Pedidos do sistema.")
        return arquivo
