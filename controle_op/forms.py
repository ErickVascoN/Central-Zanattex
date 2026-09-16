from __future__ import annotations

from django import forms

from .models import EnvioProducao, RetornoProducao, tipo_os_sugerido


class EnvioProducaoForm(forms.ModelForm):
    """`programacao=` é opcional só pra não quebrar chamada sem contexto (o
    admin, por exemplo): quando vem, o tipo de OS já nasce escolhido a partir
    do destino de costura da OP."""

    class Meta:
        model = EnvioProducao
        fields = ["data", "tipo", "numero", "destino", "quantidade_pecas", "observacao"]
        widgets = {
            # format explícito: sem ele o Django localiza pra dd/mm/aaaa (pt-br)
            # e o <input type="date"> descarta o valor, deixando o campo vazio.
            "data": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "field-input"}),
            "tipo": forms.Select(attrs={"class": "field-input"}),
            "numero": forms.TextInput(attrs={"class": "field-input", "placeholder": "nº no ERP"}),
            "destino": forms.TextInput(attrs={"class": "field-input"}),
            "quantidade_pecas": forms.NumberInput(attrs={"class": "field-input"}),
            "observacao": forms.TextInput(attrs={"class": "field-input"}),
        }

    def __init__(self, *args, programacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        if programacao is not None and not self.initial.get("tipo"):
            self.initial["tipo"] = tipo_os_sugerido(programacao.destino_costura)

    def clean_numero(self) -> str:
        """Espaço em volta e caixa baixa fariam "1234 " e "1234" passarem
        como OS diferentes — e a unicidade é o ponto do campo."""
        return (self.cleaned_data.get("numero") or "").strip().upper()


class RetornoProducaoForm(forms.ModelForm):
    class Meta:
        model = RetornoProducao
        fields = ["data", "quantidade_pecas", "retalho_kg", "observacao"]
        widgets = {
            # format explícito: sem ele o Django localiza pra dd/mm/aaaa (pt-br)
            # e o <input type="date"> descarta o valor, deixando o campo vazio.
            "data": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "field-input"}),
            "quantidade_pecas": forms.NumberInput(attrs={"class": "field-input"}),
            "retalho_kg": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "observacao": forms.TextInput(attrs={"class": "field-input"}),
        }
