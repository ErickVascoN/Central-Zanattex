"""Formulário de registro de corte (Gestão de Corte) — os campos extras
variam por unidade (ver tabela de campos por unidade levantada na exploração
do corte/servicos.py, lencol_servicos.py, cortina_servicos.py,
itaju_servicos.py). `kg_cortado` (Manta/Cobertor) e `metros_cortado`
(Lençol) são a base do cálculo de rendimento de cada uma (ver
corte/aproveitamento.py), replicando as planilhas de referência (CONTROLE DE
OPs AREALVA.xlsx pra Manta, INFORMAÇOES DE CORTES EM GERAL.xlsx pra
Lençol — Lençol mede em metros, não pesa em kg, por isso não usa
`kg_cortado`)."""
from __future__ import annotations

from django import forms

from contas.models import UnidadeCorte

from .lencol_caseamento import eh_jogo_de_cama
from .models import Cortador, EstacaoCorte, RegistroCorte

# Campos extras (guardados em RegistroCorte.extra, um JSONField) por
# unidade — (chave, rótulo, obrigatório). `gramatura`/`babys_pecas`
# (Manta/Cobertor) e `metros_por_peca` (Lençol) alimentam o cálculo de
# rendimento em corte/aproveitamento.py — ver ali a fórmula de cada
# unidade. `cliente`/`categoria` do pedido já vêm da Programação (snapshot
# da Carteira) — não pedimos de novo aqui. Mesma coisa pro `tamanho` da
# Manta — já está definido no pedido, não precisa perguntar de novo aqui.
# `cortador` (Lençol) e `estacao` (Mantas) são escolha fixa cadastrada no
# admin, não texto livre — ver MODELO_OPCAO_POR_CHAVE abaixo. `kg_por_metro`
# só existe pra Lençol — a Manta não mede rolo/peça em metros (só pesa em
# kg), então não tem o que converter; ninguém preenche ainda (não temos
# esse dado), mas o campo já existe pra quando tiverem (ver
# corte/aproveitamento.py).
CAMPOS_EXTRA_POR_UNIDADE: dict[str, list[tuple[str, str, bool]]] = {
    UnidadeCorte.AREALVA_MANTA: [
        ("cor", "Cor", True), ("estacao", "Estação de corte", True),
        ("gramatura", "Gramatura (kg/peça)", True), ("babys_pecas", "Babys (peças)", False),
    ],
    UnidadeCorte.IACANGA_MANTA: [
        ("cor", "Cor", True), ("estacao", "Estação de corte", True),
        ("gramatura", "Gramatura (kg/peça)", True), ("babys_pecas", "Babys (peças)", False),
    ],
    UnidadeCorte.LENCOL: [
        ("cortador", "Cortador", True), ("metros_por_peca", "Metros por peça", True),
        ("kg_por_metro", "Kg por metro do tecido (opcional, se souber)", False),
    ],
    UnidadeCorte.CORTINA: [("cor", "Cor", False), ("tamanho", "Tamanho (L x A)", False)],
    UnidadeCorte.ITAJU: [
        ("cor", "Cor", True), ("tamanho", "Tamanho", True), ("produto", "Parte (Cima/Fundo/Fronha/Jogo)", True),
        ("estacao", "Estação de corte", True),
    ],
}

# Campos extras de escolha fixa (não texto livre) — a lista de opções vem
# do model, filtrada por unidade e `ativo=True`, cadastrada/gerenciada pelo
# Django admin (ver corte/admin.py). Adicionar/desativar um cortador ou uma
# estação é imediato ali, sem precisar de tela própria nem migração.
MODELO_OPCAO_POR_CHAVE = {
    "cortador": Cortador,
    "estacao": EstacaoCorte,
}

# Manta/Cobertor pesa o corte na balança (kg); Lençol mede em metros — os
# dois nunca aparecem juntos no mesmo formulário. Retalho é sempre em kg
# nas 3 (Manta, Manta Iacanga, Lençol).
UNIDADES_COM_KG = {UnidadeCorte.AREALVA_MANTA, UnidadeCorte.IACANGA_MANTA}
UNIDADES_COM_METROS = {UnidadeCorte.LENCOL}
UNIDADES_COM_RETALHO = {UnidadeCorte.AREALVA_MANTA, UnidadeCorte.IACANGA_MANTA, UnidadeCorte.LENCOL}


class RegistroCorteForm(forms.ModelForm):
    class Meta:
        model = RegistroCorte
        fields = ["data", "quantidade_pecas", "kg_cortado", "metros_cortado", "retalho_kg"]
        widgets = {
            # format explícito: sem ele o Django localiza pra dd/mm/aaaa (pt-br)
            # e o <input type="date"> descarta o valor, deixando o campo vazio.
            "data": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "field-input"}),
            "quantidade_pecas": forms.NumberInput(attrs={"class": "field-input"}),
            "kg_cortado": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "metros_cortado": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "retalho_kg": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
        }

    def __init__(self, *args, unidade: str = "", programacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.unidade = unidade
        campos = list(CAMPOS_EXTRA_POR_UNIDADE.get(unidade, []))

        # Jogo de cama (duplo/simples) — mesma classificação do dash de
        # Lençol (classifica_jogo_fundo). "Peças cortadas" vira "Lençol de
        # cima" e ganha Fundo/Fronha ao lado, pra dar pra calcular o
        # caseamento no Controle de OP (ver corte/aproveitamento.py).
        self.eh_jogo_de_cama = False
        self.tamanho_jogo = ""
        if unidade == UnidadeCorte.LENCOL and programacao is not None:
            self.eh_jogo_de_cama, self.tamanho_jogo = eh_jogo_de_cama(
                programacao.categoria, programacao.produto)
            if self.eh_jogo_de_cama:
                self.fields["quantidade_pecas"].label = "Lençol de cima (peças)"
                campos = campos + [
                    ("fundo_cortado", "Fundo cortado (peças)", False),
                    ("fronha_cortado", "Fronha cortada (peças)", False),
                ]

        # Cada item de campos_extra ganha uma 4ª posição (`opcoes`, None ou
        # lista de (valor, rótulo)) — o template renderiza <select> quando
        # a chave tem um model em MODELO_OPCAO_POR_CHAVE, senão cai no
        # <input type="text"> de sempre.
        self.campos_extra = [
            (chave, rotulo, obrigatorio, self._opcoes_de(chave, unidade))
            for chave, rotulo, obrigatorio in campos
        ]
        # Subconjunto de campos_extra que o template renderiza logo depois
        # de "Lençol de cima" (não lá embaixo, misturado com cortador/metros
        # por peça) — fundo e fronha precisam ficar juntos, um do lado do
        # outro, pra bater o olho e lançar os 3 de uma vez.
        self.campos_jogo = [c for c in self.campos_extra if c[0] in ("fundo_cortado", "fronha_cortado")]

        if unidade not in UNIDADES_COM_KG:
            del self.fields["kg_cortado"]
        else:
            self.fields["kg_cortado"].required = False
        if unidade not in UNIDADES_COM_METROS:
            del self.fields["metros_cortado"]
        else:
            self.fields["metros_cortado"].required = False
        if unidade not in UNIDADES_COM_RETALHO:
            del self.fields["retalho_kg"]
        else:
            self.fields["retalho_kg"].required = False

    @staticmethod
    def _opcoes_de(chave: str, unidade: str) -> list[tuple[str, str]] | None:
        modelo = MODELO_OPCAO_POR_CHAVE.get(chave)
        if modelo is None:
            return None
        return [(o.nome, o.nome) for o in modelo.objects.filter(unidade_corte=unidade, ativo=True)]

    def extra_do_post(self, data) -> dict:
        """Monta o dict de `extra` a partir dos campos dinâmicos (não fazem
        parte do ModelForm porque variam por unidade — ver campos_extra)."""
        return {chave: data.get(f"extra_{chave}", "").strip() for chave, _, _, _ in self.campos_extra}
