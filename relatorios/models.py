from __future__ import annotations

from django.db import models


class EnvioDiario(models.Model):
    """Estado do envio automático diário (D-1) de cada relatório, um registro
    por (tipo, data_referencia). Usado pelo endpoint de cron (`relatorios/cron.py`)
    para: parar de checar depois do relatório definitivo, e não reenviar o
    parcial quando a lista de pendências não mudou desde a última checagem."""

    class Tipo(models.TextChoices):
        CORTE = "corte", "Corte"
        PRODUCAO = "producao", "Produção Diária"

    class Status(models.TextChoices):
        PARCIAL = "parcial", "Relatório parcial enviado"
        COMPLETO = "completo", "Relatório definitivo enviado"

    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    data_referencia = models.DateField("Data de referência (D-1)")
    status = models.CharField(max_length=10, choices=Status.choices)
    detalhe = models.TextField(
        "Detalhe", blank=True, default="",
        help_text="Última lista de pendências enviada (pra comparar e não repetir e-mail parcial idêntico).")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Envio diário"
        verbose_name_plural = "Envios diários"
        unique_together = ("tipo", "data_referencia")
        ordering = ["-data_referencia", "tipo"]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.data_referencia} · {self.get_status_display()}"
