"""Primeira tabela própria do app `carteira` — até aqui o app era 100%
derivado ao vivo de Google Sheets (ver `servicos.py`), sem model nenhum.

`ImportacaoCarteira` é só o registro de auditoria de quem subiu um Excel
pela tela de importação (`views.py::importar_excel`/`confirmar_importacao`)
e quando — os DADOS da carteira em si continuam indo pra tabela
sincronizada `carteira_pedidos` via `integracao.db_sync.sync_dataframe`,
igual ao que a sincronização da planilha já faz. Diferente de
`integracao.models.FonteSincronizada` (que só guarda o status da última
sincronização, não histórico): como o upload substitui a tabela inteira
por ação manual de alguém, vale manter registro de quem trocou o quê e
quando — histórico de verdade, não só o estado atual."""
from __future__ import annotations

from django.conf import settings
from django.db import models


class ImportacaoCarteira(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="importacoes_carteira")
    nome_arquivo = models.CharField(max_length=255)
    linhas_importadas = models.PositiveIntegerField()
    linhas_ignoradas = models.PositiveIntegerField(default=0)
    valor_total = models.FloatField(default=0)
    avisos = models.JSONField(default=list, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Importação de carteira"
        verbose_name_plural = "Importações de carteira"

    def __str__(self):
        return f"{self.nome_arquivo} — {self.linhas_importadas} linhas ({self.criado_em:%d/%m/%Y %H:%M})"
