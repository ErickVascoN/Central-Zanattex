"""Formulário de Nova Programação — cria uma ProgramacaoCorte a partir de um
pedido em aberto da Carteira. Os campos de snapshot (cliente/categoria/
produto/tamanho/saldo) vêm ocultos no POST, preenchidos pelo Alpine.js ao
selecionar um pedido na lista (ver templates/programacao/nova_programacao.html)
— não são digitados pelo usuário."""
from __future__ import annotations

from django import forms
from django.db.models import Sum

from contas.models import UnidadeCorte
from corte.models import UNIDADE_TO_LOCAL, ProgramacaoCorte
from producao.faccao_loader import load_faccoes

# Sugestão automática de unidade pela categoria do produto — o usuário pode
# trocar no formulário. Heurística simples, ajustável se o mix de produtos
# por unidade mudar (não há regra fixa nenhuma no ERP pra isso hoje). Mesma
# intenção que a antiga SUGESTAO_LOCAL_POR_CATEGORIA (Local só tinha 3
# opções — Giattex/Zanattex/Lençol —, então "Zanattex" não desambiguava
# entre Manta Arealva, Cortina e Itaju; agora sugere a unidade real).
SUGESTAO_UNIDADE_POR_CATEGORIA = {
    "MANTA": UnidadeCorte.IACANGA_MANTA,
    "COLCHA": UnidadeCorte.IACANGA_MANTA,
    "LENÇOL": UnidadeCorte.AREALVA_MANTA,
    "FRONHA / ACESSÓRIOS": UnidadeCorte.LENCOL,
    "CORTINA": UnidadeCorte.CORTINA,
}


def sugerir_unidade(categoria: str) -> str:
    return SUGESTAO_UNIDADE_POR_CATEGORIA.get(
        (categoria or "").upper(), UnidadeCorte.AREALVA_MANTA)


def opcoes_destino_costura() -> list[str]:
    """Facções/costuras que podem receber o corte — mesma fonte de dados da
    Análise de Produção (producao/faccao_loader.py), sem duplicar lista."""
    df = load_faccoes()
    nomes = sorted(df["FACCAO"].dropna().unique().tolist()) if not df.empty else []
    if "COSTURA INTERNA" not in nomes:
        nomes.insert(0, "COSTURA INTERNA")
    return nomes


class NovaProgramacaoForm(forms.ModelForm):
    class Meta:
        model = ProgramacaoCorte
        fields = [
            "pedido", "op_interna", "oc",
            "cliente", "categoria", "produto", "tamanho", "saldo_carteira_snap",
            "data_entrada_carteira",
            "qnt_programada", "semana", "unidade_corte", "destino_costura",
            "prev_corte", "observacao",
        ]
        widgets = {
            "pedido": forms.HiddenInput(),
            "cliente": forms.HiddenInput(),
            "categoria": forms.HiddenInput(),
            "produto": forms.HiddenInput(),
            "tamanho": forms.HiddenInput(),
            "saldo_carteira_snap": forms.HiddenInput(),
            "data_entrada_carteira": forms.HiddenInput(),
            "prev_corte": forms.DateInput(attrs={"type": "date"}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destino_costura"].widget = forms.Select(
            choices=[("", "Selecione a facção/produção…")]
            + [(nome, nome) for nome in opcoes_destino_costura()])
        self.fields["op_interna"].required = False
        self.fields["oc"].required = False
        self.fields["observacao"].required = False
        self.fields["data_entrada_carteira"].required = False
        self.fields["prev_corte"].required = False
        self.fields["unidade_corte"].required = True

    def clean(self):
        cleaned = super().clean()
        pedido = cleaned.get("pedido")
        qnt = cleaned.get("qnt_programada")
        saldo_snap = cleaned.get("saldo_carteira_snap")
        if pedido and qnt:
            ja_programado = (
                ProgramacaoCorte.objects
                .filter(pedido=pedido)
                .exclude(status__in=ProgramacaoCorte.STATUS_FECHADOS)
                .aggregate(total=Sum("qnt_programada"))["total"] or 0
            )
            if saldo_snap is not None and (ja_programado + qnt) > saldo_snap:
                # Aviso, não bloqueio — o saldo real da Carteira pode ter
                # mudado desde que a lista foi carregada.
                self.add_warning = (
                    f"Atenção: {ja_programado + qnt} peças programadas pra esse pedido, "
                    f"mas o saldo era {saldo_snap} no momento em que a lista foi aberta. "
                    "Confira antes de reprogramar."
                )
        return cleaned

    def save(self, commit=True):
        # `local` não é escolhido no formulário (a tela pede a unidade real,
        # mais granular) — deriva automaticamente pra manter funcionando
        # tudo que já agrupa/filtra por local (relatório pro grupo do PCP,
        # exports, "Pendentes de semanas anteriores" etc.).
        instance = super().save(commit=False)
        instance.local = UNIDADE_TO_LOCAL.get(instance.unidade_corte, instance.local)
        if commit:
            instance.save()
        return instance


class EditarProgramacaoForm(forms.ModelForm):
    """Corrige uma OP já programada — não mexe no vínculo com a Carteira
    (pedido/cliente/produto/tamanho/saldo continuam o snapshot original,
    isso aqui não troca de pedido, só corrige local/quantidade/destino/
    prazo/observação). Se já tiver corte lançado, `qnt_programada` não pode
    cair abaixo do que já foi cortado — ver clean()."""

    class Meta:
        model = ProgramacaoCorte
        fields = [
            "op_interna", "oc", "unidade_corte", "destino_costura",
            "qnt_programada", "prev_corte", "observacao",
        ]
        widgets = {
            "prev_corte": forms.DateInput(attrs={"type": "date", "class": "field-input"}),
            "observacao": forms.Textarea(attrs={"rows": 3, "class": "field-input"}),
            "op_interna": forms.TextInput(attrs={"class": "field-input"}),
            "oc": forms.TextInput(attrs={"class": "field-input"}),
            "qnt_programada": forms.NumberInput(attrs={"class": "field-input"}),
            "unidade_corte": forms.Select(attrs={"class": "field-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destino_costura"].widget = forms.Select(
            attrs={"class": "field-input"},
            choices=[("", "Selecione a facção/produção…")]
            + [(nome, nome) for nome in opcoes_destino_costura()])
        self.fields["op_interna"].required = False
        self.fields["oc"].required = False
        self.fields["observacao"].required = False
        self.fields["prev_corte"].required = False
        self.fields["unidade_corte"].required = True

    def save(self, commit=True):
        # Mesma derivação automática de `local` a partir de `unidade_corte`
        # que NovaProgramacaoForm faz — ver ali pra justificativa.
        instance = super().save(commit=False)
        instance.local = UNIDADE_TO_LOCAL.get(instance.unidade_corte, instance.local)
        if commit:
            instance.save()
        return instance

    def clean_qnt_programada(self):
        qnt = self.cleaned_data["qnt_programada"]
        if self.instance.pk:
            ja_cortado = self.instance.registros.aggregate(
                total=Sum("quantidade_pecas"))["total"] or 0
            if qnt < ja_cortado:
                raise forms.ValidationError(
                    f"Já foram cortadas {ja_cortado} peças dessa OP — não dá pra "
                    f"programar menos que isso.")
        return qnt
