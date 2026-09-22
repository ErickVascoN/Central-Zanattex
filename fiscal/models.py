"""
Saldo Fiscal — controle de industrialização por encomenda.

A Zanattex trabalha só como terceirizada: o cliente manda tecido/insumo por
uma NF de remessa (entrada da Zanattex) e recebe de volta as sobras não
usadas + o produto pronto por uma NF de retorno (saída). O que este app
guarda é exatamente essa cadeia: quanto entrou, quanto voltou, quanto ainda
falta devolver — por cliente e por NF de entrada.

Todo valor fiscal (quantidade, dinheiro) é DecimalField, nunca float — é
contabilidade que o cliente pode cobrar prestação de contas, não pode
carregar erro de arredondamento de ponto flutuante.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class Cliente(models.Model):
    """Empresa dona do tecido/insumo que a Zanattex recebe pra processar.
    CNPJ é a chave de identificação automática na importação do XML (ver
    fiscal/nfe_xml.py::identificar_nota) — nunca criado sozinho pelo
    importador quando não bate com nenhum cadastro aqui (ver
    PendenciaMatching.Motivo.CNPJ_SEM_CLIENTE)."""
    nome = models.CharField("Razão social", max_length=200)
    cnpj = models.CharField("CNPJ", max_length=14, unique=True, db_index=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

    def __str__(self) -> str:
        return self.nome


class NotaFiscal(models.Model):
    """Cabeçalho de uma NF-e — entrada (remessa recebida do cliente) ou
    saída (retorno da Zanattex pro cliente). Um único model pros dois tipos
    porque compartilham quase todos os campos de cabeçalho e a tela de
    Histórico precisa listar os dois juntos."""

    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada (remessa recebida)"
        SAIDA = "SAIDA", "Saída (retorno)"

    class Status(models.TextChoices):
        OK = "OK", "OK"
        PENDENTE = "PENDENTE", "Pendente de conferência"
        ERRO = "ERRO", "Erro no processamento"

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="notas")
    tipo = models.CharField(max_length=10, choices=Tipo.choices, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OK, db_index=True)

    # chave_acesso é a chave real de dedupe (globalmente única); n_nf não é
    # — só é única por emitente+série — mas é o que o infAdProd referencia
    # (ver fiscal/matching.py), por isso também fica indexada.
    chave_acesso = models.CharField("Chave de acesso", max_length=44, unique=True, db_index=True)
    n_nf = models.CharField("Número da NF", max_length=20, db_index=True)
    serie = models.CharField("Série", max_length=5, blank=True)
    nat_op = models.CharField("Natureza da operação", max_length=120, blank=True)
    data_emissao = models.DateTimeField("Data de emissão")

    emit_cnpj = models.CharField("CNPJ emitente", max_length=14)
    emit_nome = models.CharField("Emitente", max_length=200, blank=True)
    dest_cnpj = models.CharField("CNPJ destinatário", max_length=14)
    dest_nome = models.CharField("Destinatário", max_length=200, blank=True)

    # Unidade/planta da Zanattex nessa NF (emit na saída, dest na entrada) —
    # extraído do xFant (nome fantasia), com fallback pro município quando o
    # XML não traz xFant. Filtro de Relatórios/Histórico.
    centro_custo = models.CharField("Centro de custo", max_length=120, blank=True, db_index=True)

    valor_total = models.DecimalField("Valor total", max_digits=14, decimal_places=2, default=0)

    arquivo_origem = models.CharField("Arquivo de origem", max_length=255, blank=True)
    importado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    importado_em = models.DateTimeField(auto_now_add=True)
    xml_bruto = models.TextField("XML original", blank=True)

    class Meta:
        ordering = ["-data_emissao"]
        indexes = [
            models.Index(fields=["cliente", "n_nf"]),
            models.Index(fields=["cliente", "tipo", "status"]),
        ]
        verbose_name = "Nota fiscal"
        verbose_name_plural = "Notas fiscais"

    def __str__(self) -> str:
        return f"NF {self.n_nf} ({self.get_tipo_display()}) — {self.cliente}"


class NotaFiscalItem(models.Model):
    """Um item (produto) dentro de uma NF. `saldo_atual` só faz sentido em
    itens de ENTRADA — é o saldo denormalizado (não somado on-the-fly a
    cada tela) que mantém os relatórios rápidos mesmo com dezenas de
    milhares de linhas de histórico. Inicializado com `q_com` na entrada,
    decrementado transacionalmente a cada baixa (ver fiscal/matching.py)."""

    class TipoRetorno(models.TextChoices):
        DEVOLUCAO_INSUMO = "DEVOLUCAO_INSUMO", "Devolução de insumo não utilizado"
        ENTREGA_PRODUTO = "ENTREGA_PRODUTO", "Entrega de produto industrializado"
        NA = "NA", "Não aplicável (item de entrada)"

    nota_fiscal = models.ForeignKey(NotaFiscal, on_delete=models.CASCADE, related_name="itens")
    n_item = models.PositiveSmallIntegerField("Nº do item")

    c_prod = models.CharField("Código do produto", max_length=60, db_index=True)
    x_prod = models.CharField("Descrição", max_length=300)
    ncm = models.CharField("NCM", max_length=8, blank=True, db_index=True)
    cfop = models.CharField("CFOP", max_length=4, db_index=True)
    u_com = models.CharField("Unidade", max_length=10, blank=True)
    q_com = models.DecimalField("Quantidade", max_digits=14, decimal_places=4)
    v_un_com = models.DecimalField("Valor unitário", max_digits=16, decimal_places=10, default=0)
    v_prod = models.DecimalField("Valor total do item", max_digits=14, decimal_places=2, default=0)

    inf_ad_prod = models.CharField(
        "infAdProd", max_length=60, blank=True, db_index=True,
        help_text="Nº da NF de entrada de onde esse item está sendo baixado (só em itens de saída).")
    tipo_retorno = models.CharField(
        max_length=20, choices=TipoRetorno.choices, default=TipoRetorno.NA)

    saldo_atual = models.DecimalField(
        "Saldo atual", max_digits=14, decimal_places=4, null=True, blank=True,
        help_text="Só preenchido em itens de entrada — quantidade ainda não devolvida.")

    class Meta:
        ordering = ["nota_fiscal", "n_item"]
        indexes = [
            models.Index(fields=["nota_fiscal", "ncm"]),
            models.Index(fields=["nota_fiscal", "c_prod"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["nota_fiscal", "n_item"], name="fiscal_item_unico_por_nf"),
        ]
        verbose_name = "Item de nota fiscal"
        verbose_name_plural = "Itens de nota fiscal"

    def __str__(self) -> str:
        return f"{self.x_prod} ({self.nota_fiscal.n_nf}, item {self.n_item})"

    @property
    def percentual_consumido(self) -> float | None:
        """% já devolvido/consumido desse item de entrada, ou None quando
        não se aplica (item de saída, ou quantidade recebida zerada)."""
        if self.saldo_atual is None or not self.q_com:
            return None
        consumido = self.q_com - self.saldo_atual
        return float(consumido / self.q_com * 100)


class Vinculo(models.Model):
    """A baixa em si: 1 item de saída (devolução) deduzindo de 1 item de
    entrada. Um item de saída tem no máximo um vínculo — se um dia precisar
    ratear entre mais de uma entrada, essa constraint muda; não visto nos
    XMLs reais até agora."""
    saida_item = models.ForeignKey(
        NotaFiscalItem, on_delete=models.CASCADE, related_name="vinculos_saida")
    entrada_item = models.ForeignKey(
        NotaFiscalItem, on_delete=models.PROTECT, related_name="vinculos_entrada")
    quantidade_baixada = models.DecimalField(max_digits=14, decimal_places=4)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["saida_item"], name="fiscal_vinculo_unico_por_item_saida"),
        ]
        verbose_name = "Vínculo (baixa)"
        verbose_name_plural = "Vínculos (baixas)"

    def __str__(self) -> str:
        return f"{self.saida_item.x_prod}: -{self.quantidade_baixada} → NF {self.entrada_item.nota_fiscal.n_nf}"


class PendenciaMatching(models.Model):
    """Fila de casos que não casaram sozinhos na importação — nunca
    descartados em silêncio. `saida_item` fica em branco quando o problema
    é anterior a existir qualquer item (ex.: CNPJ do XML sem Cliente
    cadastrado — a importação inteira daquele arquivo para antes disso)."""

    class Motivo(models.TextChoices):
        NF_NAO_ENCONTRADA = "NF_NAO_ENCONTRADA", "NF de entrada (infAdProd) não encontrada"
        ITEM_AMBIGUO = "ITEM_AMBIGUO", "Mais de um item da entrada é candidato"
        ITEM_NAO_ENCONTRADO = "ITEM_NAO_ENCONTRADO", "Nenhum item da entrada bate por NCM/código"
        SALDO_INSUFICIENTE = "SALDO_INSUFICIENTE", "Devolução maior que o saldo disponível"
        CNPJ_SEM_CLIENTE = "CNPJ_SEM_CLIENTE", "CNPJ do XML sem Cliente cadastrado"

    saida_item = models.ForeignKey(
        NotaFiscalItem, on_delete=models.CASCADE, related_name="pendencias", null=True, blank=True)
    motivo = models.CharField(max_length=30, choices=Motivo.choices)
    detalhe = models.TextField("Detalhe", blank=True)

    resolvido = models.BooleanField(default=False)
    resolvido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    resolvido_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["resolvido", "-criado_em"]
        verbose_name = "Pendência de casamento fiscal"
        verbose_name_plural = "Pendências de casamento fiscal"

    def __str__(self) -> str:
        return f"{self.get_motivo_display()} — {self.saida_item or 'importação'}"
