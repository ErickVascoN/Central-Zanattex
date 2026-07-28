"""Regras de premiação por produtividade (GGTTEX Jogos e Fronha) — ANEXO I.

Metas diárias e valor por peça excedente ficam configuráveis via admin (não
fixos no código) para permitir reajuste sem deploy. `vigente_desde`/
`vigente_ate` preservam o cálculo histórico de meses já fechados quando a
regra for reajustada no futuro.
"""

from __future__ import annotations

from django.db import models


class RegraPremiacao(models.Model):
    class Unidade(models.TextChoices):
        GGTTEX_JOGOS = "GGTTEX_JOGOS", "GGTTEX Jogos"
        GGTTEX_FRONHA = "GGTTEX_FRONHA", "GGTTEX Fronha"

    class Atividade(models.TextChoices):
        COSTURA_CANTO = "COSTURA_CANTO", "Costura de Canto"
        COSTURA_ELASTICO = "COSTURA_ELASTICO", "Costura de Elástico"
        COSTURA_RETA = "COSTURA_RETA", "Costura Reta"
        DOBRA_FUNDO = "DOBRA_FUNDO", "Dobra de Fundo"
        DOBRA_EMPAPELA = "DOBRA_EMPAPELA", "Dobra e Empapela"
        CASEADO = "CASEADO", "Caseado"
        EMBALAGEM = "EMBALAGEM", "Embalagem"
        BAINHA_FECHAMENTO = "BAINHA_FECHAMENTO", "Bainha ou Fechamento"
        ESTICADO = "ESTICADO", "Esticado"
        DESVIRADO = "DESVIRADO", "Desvirado"

    unidade = models.CharField(max_length=20, choices=Unidade.choices)
    atividade = models.CharField(max_length=30, choices=Atividade.choices)

    # True só para Costura de Canto/Elástico: meta varia por tamanho produzido
    # no dia. Demais atividades usam meta_padrao (flat).
    usa_tamanho = models.BooleanField(
        "Meta varia por tamanho",
        default=False,
        help_text="Marque só para atividades cuja meta depende do tamanho "
                   "(Solteiro/Casal/Queen) produzido no dia.",
    )
    meta_solteiro = models.PositiveIntegerField(
        "Meta — Solteiro (peças/dia)", null=True, blank=True)
    meta_casal = models.PositiveIntegerField(
        "Meta — Casal (peças/dia)", null=True, blank=True)
    meta_queen = models.PositiveIntegerField(
        "Meta — Queen (peças/dia)", null=True, blank=True)
    meta_tamanhos_mistos = models.PositiveIntegerField(
        "Meta — mais de um tamanho no dia (peças/dia)", null=True, blank=True,
        help_text="Usada quando o colaborador produz mais de um tamanho na "
                   "mesma atividade no mesmo dia.",
    )
    meta_padrao = models.PositiveIntegerField(
        "Meta padrão (peças/dia)", null=True, blank=True,
        help_text="Usada quando a atividade não varia por tamanho.",
    )

    valor_peca_excedente = models.DecimalField(
        "Valor por peça excedente (R$)", max_digits=6, decimal_places=2)

    vigente_desde = models.DateField("Vigente desde")
    vigente_ate = models.DateField(
        "Vigente até", null=True, blank=True,
        help_text="Deixe em branco se ainda está em vigor.",
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Regra de premiação"
        verbose_name_plural = "Regras de premiação"
        ordering = ["unidade", "atividade", "-vigente_desde"]

    def __str__(self):
        return f"{self.get_atividade_display()} · {self.get_unidade_display()}"
