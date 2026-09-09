"""Tabelas transacionais do fluxo Programação → Corte real. Antes deste
model, Programação de Corte e Corte eram 100% derivados ao vivo de Google
Sheets (ver programacao/servicos.py, corte/servicos.py) — este é o primeiro
passo pra virar sistema de controle de verdade, mantendo a planilha como
fallback (corte/sync.py continua rodando sem alteração)."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from contas.models import UnidadeCorte


class ProgramacaoCorte(models.Model):
    """Uma OP programada pra cortar numa semana/local — criada pela tela de
    Nova Programação (programacao/views.py), a partir de um pedido em
    aberto da Carteira. Snapshot dos dados do pedido no momento da
    programação (cliente/produto/saldo), não FK viva pra Carteira — ela
    continua só leitura de planilha, sem model próprio."""

    class Local(models.TextChoices):
        GIATTEX = "GIATTEX", "Giattex (Iacanga)"
        ZANATTEX = "ZANATTEX", "Zanattex (Arealva)"
        LENCOL = "LENCOL", "Lençol (Arealva)"

    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        PARCIAL = "PARCIAL", "Parcial"
        CONCLUIDO = "CONCLUIDO", "Concluído"
        CANCELADO = "CANCELADO", "Cancelado"
        REPROGRAMADO = "REPROGRAMADO", "Reprogramado"

    # Status "fechados" — não contam mais como pendência ativa (não aparecem
    # na fila de Gestão de Corte, não entram na soma de "já programado" ao
    # calcular saldo da Carteira). Cancelado = programação errada, descartada;
    # Reprogramado = o saldo restante virou uma OP nova (ver campo
    # `reprogramada_de` abaixo) — a linha antiga fica só como histórico.
    STATUS_FECHADOS = [Status.CANCELADO, Status.REPROGRAMADO]

    class Origem(models.TextChoices):
        SISTEMA = "SISTEMA", "Sistema"
        IMPORTACAO = "IMPORTACAO", "Importação (planilha)"

    # Marca se a OP foi programada pela tela nova (Nova Programação) ou
    # veio do backfill de cutover (corte/management/commands/backfill_
    # programacao.py). A Gestão de Corte só lista `SISTEMA` — o backlog
    # importado da planilha continua sendo acompanhado do jeito antigo
    # (dashboard/planilha) até ser reprogramado pelo sistema novo.
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.SISTEMA)

    # Preenchido quando esta linha nasceu de um "Reprogramar" em cima de uma
    # OP Pendente/Parcial de semana anterior — aponta pra linha original
    # (que fica com status=REPROGRAMADO). Histórico de quem veio de onde.
    reprogramada_de = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reprogramacoes")

    # Vínculo com a Carteira — por chave (PEDIDO), não FK viva
    pedido = models.CharField("Pedido", max_length=50, db_index=True)
    op_interna = models.CharField("OP Interna", max_length=50, blank=True)
    oc = models.CharField("OC", max_length=50, blank=True)

    # Snapshot do pedido no momento em que foi programado
    cliente = models.CharField(max_length=120)
    categoria = models.CharField(max_length=60, blank=True)
    produto = models.CharField(max_length=120)
    tamanho = models.CharField(max_length=30, blank=True)
    saldo_carteira_snap = models.PositiveIntegerField("Saldo em aberto no momento")

    qnt_programada = models.PositiveIntegerField("Qtd. a programar")
    semana = models.CharField(max_length=10)  # ex.: "2026-S34"
    local = models.CharField(
        max_length=20, choices=Local.choices,
        help_text="Derivado automaticamente de `unidade_corte` — ver UNIDADE_TO_LOCAL "
                   "abaixo. Continua existindo pra não quebrar filtros/relatórios já "
                   "agrupados por local (ex.: relatório pro grupo do PCP).")
    unidade_corte = models.CharField(
        "Unidade de corte", max_length=20, choices=UnidadeCorte.choices, blank=True,
        help_text="Unidade real que vai cortar — granularidade maior que `local` (ex.: "
                   "local=ZANATTEX pode ser Manta Arealva, Cortina ou Itaju). É o que a "
                   "tela de Nova Programação pede pra escolher, e o que a fila de Gestão "
                   "de Corte usa pra filtrar sem ambiguidade. Em branco = OP antiga "
                   "(backfill/pré-migração), a fila cai de volta no agrupamento por local.")
    destino_costura = models.CharField(
        "Destino (produção)", max_length=120,
        help_text="Facção/produção que recebe o corte — mesma lista de "
                   "producao.faccao_loader.load_faccoes().")

    data_inicio = models.DateField(null=True, blank=True)
    prev_industrializacao = models.DateField("Previsão de industrialização", null=True, blank=True)
    data_finalizado = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    observacao = models.TextField(blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="programacoes_criadas")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["pedido"]),
            models.Index(fields=["semana", "local"]),
        ]
        verbose_name = "Programação de corte"
        verbose_name_plural = "Programações de corte"

    def __str__(self):
        return f"{self.pedido or self.op_interna} — {self.cliente} ({self.get_local_display()})"


# Toda unidade real de corte cai em exatamente um `local` de planejamento —
# usado pra derivar `local` automaticamente a partir de `unidade_corte`
# (ver NovaProgramacaoForm/EditarProgramacaoForm em programacao/forms.py) e,
# antes disso, era a mesma informação duplicada como `_LOCAIS_DA_UNIDADE`
# em corte/views.py (agora reaproveita esta).
UNIDADE_TO_LOCAL = {
    UnidadeCorte.AREALVA_MANTA: ProgramacaoCorte.Local.ZANATTEX,
    UnidadeCorte.IACANGA_MANTA: ProgramacaoCorte.Local.GIATTEX,
    UnidadeCorte.LENCOL: ProgramacaoCorte.Local.LENCOL,
    UnidadeCorte.CORTINA: ProgramacaoCorte.Local.ZANATTEX,
    UnidadeCorte.ITAJU: ProgramacaoCorte.Local.ZANATTEX,
}


class RegistroCorte(models.Model):
    """Resultado REAL de um corte, lançado por alguém do setor Corte na
    tela de Gestão de Corte. Várias linhas por OP são esperadas (cortes
    parciais ao longo de vários dias) — mesma lógica que
    programacao/servicos.py::_status_corte() já assume hoje pro cruzamento
    com a planilha."""

    programacao = models.ForeignKey(
        ProgramacaoCorte, on_delete=models.PROTECT, related_name="registros")
    unidade = models.CharField(max_length=20, choices=UnidadeCorte.choices)
    data = models.DateField()

    quantidade_pecas = models.PositiveIntegerField("Peças cortadas")
    kg_cortado = models.DecimalField(
        "Kg cortado", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Manta/Cobertor — pesado na balança. Lençol usa `metros_cortado` "
                   "em vez disso (não pesa em kg, mede em metros).")
    metros_cortado = models.DecimalField(
        "Metros cortados", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Só Lençol — medido do rolo, não é sempre igual a "
                   "peças×metros_por_peça (pode ter perda/emenda no meio).")
    retalho_kg = models.DecimalField(
        "Retalho (kg)", max_digits=10, decimal_places=2, null=True, blank=True)

    # Campos secundários que variam por unidade (cor, tamanho, estação,
    # categoria, prestador, empresa, valor_peça, obs) — ver corte/forms.py
    # pra quais aparecem no mini-formulário de cada unidade.
    extra = models.JSONField(default=dict, blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="registros_corte")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criado_em"]
        verbose_name = "Registro de corte"
        verbose_name_plural = "Registros de corte"

    def __str__(self):
        return f"{self.programacao} — {self.data} ({self.quantidade_pecas} pçs)"


class Cortador(models.Model):
    """Lista de cortadores por unidade — hoje só usada pelo Lençol (o
    "Prestador" de texto livre virou esse select, já que os cortadores são
    um grupo fixo de pessoas, não algo que muda a cada OP). Gerenciado pelo
    Django admin (adicionar/desativar é rápido, sem precisar de tela
    própria) — desativar em vez de excluir preserva o histórico já lançado
    com `extra.cortador` apontando pro nome dele."""
    nome = models.CharField(max_length=80)
    unidade_corte = models.CharField(max_length=20, choices=UnidadeCorte.choices)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        unique_together = [("nome", "unidade_corte")]
        verbose_name = "Cortador"
        verbose_name_plural = "Cortadores"

    def __str__(self):
        return f"{self.nome} ({self.get_unidade_corte_display()})"


class EstacaoCorte(models.Model):
    """Lista de estações de corte por unidade — usada pela Manta Iacanga
    (Giattex) e Manta Arealva (o "Estação de corte" de texto livre virou
    esse select, mesma lógica do Cortador do Lençol: são estações físicas
    fixas, não algo que muda a cada OP). Gerenciado pelo Django admin."""
    nome = models.CharField(max_length=80)
    unidade_corte = models.CharField(max_length=20, choices=UnidadeCorte.choices)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        unique_together = [("nome", "unidade_corte")]
        verbose_name = "Estação de corte"
        verbose_name_plural = "Estações de corte"

    def __str__(self):
        return f"{self.nome} ({self.get_unidade_corte_display()})"
