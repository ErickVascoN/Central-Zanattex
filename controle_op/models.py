"""Tabelas do pós-corte — o pedaço do fluxo que fica entre "Corte Gerado" e
"Baixar Ordem de Produção" no diagrama de processos da Zanattex: definição de
onde a OP vai ser industrializada, envio das peças cortadas pro prestador
(interno ou externo), e o retorno (peças prontas + retalho gerado na
produção). `corte/models.py` já cobre a ponta de programação/corte; este
módulo cobre a ponta de produção — junto, os dois dão o rollup completo que
`controle_op/producao.py` e as telas deste app consomem.

Não há tabela própria de "produção diária": ela é lida ao vivo da planilha de
facções (`producao.faccao_loader.load_faccoes()`, já usada pela Análise de
Produção) e casada com a OP por cliente+produto+facção — ver
`controle_op/producao.py::producao_diaria_auto()`. Isso evita pedir de novo
uma informação que já existe automatizada; só o que não tem fonte viva hoje
(envio/retorno/faturamento) vira lançamento manual aqui."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from corte.models import ProgramacaoCorte
from integracao.normalize import normalize_text


DESTINO_INTERNO = "COSTURA INTERNA"


def tipo_os_sugerido(destino: str) -> str:
    """OSI quando a costura é da própria casa, OSE quando vai pra facção —
    só um palpite inicial pro formulário (o usuário troca à vontade). O nome
    "COSTURA INTERNA" é o mesmo item fixo que `programacao/forms.py::
    opcoes_destino_costura()` insere na lista de destinos."""
    return (EnvioProducao.Tipo.OSI
            if normalize_text(destino) == DESTINO_INTERNO
            else EnvioProducao.Tipo.OSE)


class EnvioProducao(models.Model):
    """Peças cortadas saindo pra industrialização (interna ou externa) —
    "Envio aos Prestadores" no diagrama de processos. Várias linhas por OP
    são esperadas (remessas parciais, mais de um prestador na mesma OP).

    É aqui que mora o vínculo com o ERP: cada remessa é uma OS (OSE externa
    ou OSI interna) que lá existe solta, sem ligação com o pedido nem com o
    corte. Guardar `tipo` + `numero` junto da OP é o que costura os dois
    lados — daí a OS sem número virar pendência da OP, não detalhe
    cosmético."""

    class Tipo(models.TextChoices):
        OSE = "OSE", "OSE — industrialização externa (facção)"
        OSI = "OSI", "OSI — industrialização interna"

    programacao = models.ForeignKey(
        ProgramacaoCorte, on_delete=models.PROTECT, related_name="envios_producao")
    data = models.DateField()
    # default só por causa das linhas que já existiam antes deste campo: o
    # envio externo é o caso comum, e o formulário sempre manda o valor
    # escolhido (pré-selecionado por tipo_os_sugerido()).
    tipo = models.CharField(
        "Tipo de OS", max_length=3, choices=Tipo.choices, default=Tipo.OSE)
    numero = models.CharField(
        "Número da OS (ERP)", max_length=30, blank=True, db_index=True,
        help_text="Número que o ERP deu pra esta OS. Pode ficar vazio agora "
                  "(o envio físico às vezes acontece antes da OS ser emitida), "
                  "mas vira pendência que impede a baixa da OP.")
    destino = models.CharField(
        "Enviado para", max_length=120,
        help_text="Facção/prestador que recebe — sugerido a partir do destino_costura da OP, editável.")
    quantidade_pecas = models.PositiveIntegerField("Peças enviadas")
    observacao = models.TextField(blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="envios_producao_criados")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criado_em"]
        verbose_name = "Envio para produção"
        verbose_name_plural = "Envios para produção"
        constraints = [
            # 1 OS = 1 pedido: o mesmo número repetido é erro de digitação,
            # e deixar passar quebraria justamente o vínculo que este campo
            # existe pra criar. Condicional porque vazio não é duplicata —
            # várias OS ainda sem número podem conviver.
            models.UniqueConstraint(
                fields=["tipo", "numero"],
                condition=~models.Q(numero=""),
                name="envio_os_numero_unico",
                violation_error_message="Já existe uma OS com este tipo e número.",
            ),
        ]

    def __str__(self):
        return f"{self.os_label} {self.programacao} → {self.destino} ({self.quantidade_pecas} pçs, {self.data})"

    @property
    def sem_numero(self) -> bool:
        return not self.numero.strip()

    @property
    def os_label(self) -> str:
        """"OSE 1234" ou "OSE (sem número)" — usado na tela, no PDF e no
        __str__ pra a OS aparecer sempre do mesmo jeito."""
        return f"{self.tipo} {self.numero}" if self.numero else f"{self.tipo} (sem número)"


class RetornoProducao(models.Model):
    """Peças voltando prontas da industrialização + retalho gerado no
    processo de produção (diferente do retalho de corte, já capturado em
    RegistroCorte — este é o retalho que sobra na costura/acabamento).
    "Retorno de Industrialização" no diagrama de processos."""

    programacao = models.ForeignKey(
        ProgramacaoCorte, on_delete=models.PROTECT, related_name="retornos_producao")
    data = models.DateField()
    quantidade_pecas = models.PositiveIntegerField("Peças retornadas (prontas)")
    retalho_kg = models.DecimalField(
        "Retalho da produção (kg)", max_digits=10, decimal_places=2, null=True, blank=True)
    observacao = models.TextField(blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="retornos_producao_criados")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criado_em"]
        verbose_name = "Retorno de produção"
        verbose_name_plural = "Retornos de produção"

    def __str__(self):
        return f"{self.programacao} ({self.quantidade_pecas} pçs, {self.data})"


class FechamentoOP(models.Model):
    """Confirmação manual do fechamento de faturamento — o único dos 3
    fechamentos (Corte / Produção / Faturamento) que não é calculado no
    sistema, porque o faturamento de verdade acontece no ERP. Aqui só se
    registra "sim, conferi no ERP e está faturado", pra a OP poder ser dada
    como encerrada de ponta a ponta num lugar só. Corte e Produção continuam
    sendo status CALCULADOS (ver corte/aproveitamento.py e
    controle_op/producao.py) — não duplicados aqui."""

    programacao = models.OneToOneField(
        ProgramacaoCorte, on_delete=models.CASCADE, related_name="fechamento")
    faturamento_confirmado = models.BooleanField(default=False)
    faturamento_confirmado_em = models.DateTimeField(null=True, blank=True)
    faturamento_confirmado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="fechamentos_confirmados")
    observacao = models.TextField(blank=True)

    class Meta:
        verbose_name = "Fechamento de OP"
        verbose_name_plural = "Fechamentos de OP"

    def __str__(self):
        return f"{self.programacao} — faturamento {'confirmado' if self.faturamento_confirmado else 'pendente'}"
