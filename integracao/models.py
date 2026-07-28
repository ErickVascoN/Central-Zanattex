from __future__ import annotations

from django.db import models


class FonteSincronizada(models.Model):
    """Status da sincronização Sheets -> Postgres de cada fonte registrada em
    `integracao.sync_registry`. Uma linha por fonte, sempre a mais recente
    (não é histórico)."""

    class Status(models.TextChoices):
        OK = "OK", "OK"
        ERRO = "ERRO", "Erro"
        NUNCA = "NUNCA", "Nunca sincronizada"

    chave = models.CharField("Chave", max_length=100, unique=True)
    tabela = models.CharField("Tabela", max_length=100)
    label = models.CharField("Nome", max_length=200)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.NUNCA)
    linhas = models.PositiveIntegerField("Linhas", null=True, blank=True)
    ultima_sincronizacao = models.DateTimeField(
        "Última sincronização", null=True, blank=True)
    erro = models.TextField("Erro", blank=True, default="")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Fonte sincronizada"
        verbose_name_plural = "Fontes sincronizadas"
        ordering = ["label"]

    def __str__(self):
        return self.label or self.chave
