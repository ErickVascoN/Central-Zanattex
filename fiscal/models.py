"""
Saldo Fiscal — controle de industrialização por encomenda.

A Zanattex trabalha só como terceirizada: o cliente manda tecido/insumo por
uma NF de remessa (entrada da Zanattex) e recebe de volta o produto pronto,
junto com uma NF declarando quanto de tecido foi de fato usado na
industrialização daquele produto (e, à parte, eventuais perdas no
processo) — é essa quantidade usada/perdida que baixa do saldo, não sobra
que ficou sem uso. O que este app guarda é exatamente essa cadeia: quanto
entrou, quanto já foi consumido, quanto ainda resta de saldo — por cliente
e por NF de entrada.

Todo valor fiscal (quantidade, dinheiro) é DecimalField, nunca float — é
contabilidade que o cliente pode cobrar prestação de contas, não pode
carregar erro de arredondamento de ponto flutuante.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


def formatar_cnpj(cnpj: str) -> str:
    if len(cnpj) != 14 or not cnpj.isdigit():
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


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


class CentroCusto(models.Model):
    """Unidade/filial da própria Zanattex que emite ou recebe as NF-e deste
    módulo — cadastro que substitui o antigo settings.FISCAL_CNPJS_ZANATTEX
    (hardcoded, exigia deploy pra cadastrar uma unidade nova). É essa lista
    que decide, no importador, qual CNPJ é "nós" (ver
    fiscal/importador.py::identificar_nota): nota cujo emit/dest não bate com
    nenhum CNPJ ativo aqui é rejeitada (XmlInvalido), nunca vira centro de
    custo novo sozinha."""
    nome = models.CharField("Nome", max_length=200)
    cnpj = models.CharField("CNPJ", max_length=14, unique=True, db_index=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Centro de custo"
        verbose_name_plural = "Centros de custo"

    def __str__(self) -> str:
        return f"{self.nome} ({formatar_cnpj(self.cnpj)})"


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

    class Situacao(models.TextChoices):
        """Se a nota vale pro saldo. Só VALIDA cria saldo (entrada) ou baixa
        (saída) — as outras ficam gravadas pra consulta, mas fora da conta."""
        VALIDA = "VALIDA", "Válida"
        NAO_AUTORIZADA = "NAO_AUTORIZADA", "Sem autorização de uso (sem protocolo ou cStat recusado)"
        ESTORNO = "ESTORNO", "NF de entrada própria (tpNF=0) — estorno/anulação de outra NF"
        ANULADA = "ANULADA", "Anulada por NF de estorno"
        CANCELADA = "CANCELADA", "Marcada como cancelada (conferido na SEFAZ)"
        EXCLUIDA = "EXCLUIDA", "Excluída do controle de saldo (decisão manual, não confirmada como cancelada)"

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="notas")
    tipo = models.CharField(max_length=10, choices=Tipo.choices, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OK, db_index=True)
    situacao = models.CharField(
        max_length=20, choices=Situacao.choices, default=Situacao.VALIDA, db_index=True)
    # tpNF do XML: "1" = nota de saída do emitente, "0" = nota de entrada
    # emitida pelo próprio emitente (é assim que a Zanattex anula uma saída
    # que já não dá mais pra cancelar: "ENTRADA REF NF 40235").
    tp_nf = models.CharField("tpNF", max_length=1, blank=True)
    # Protocolo de autorização presente com cStat 100 (autorizada) ou 150
    # (autorizada fora de prazo). XML sem protocolo = nota nunca autorizada.
    autorizada = models.BooleanField(default=True)
    # Pra ANULADA: a nota de estorno que anulou esta.
    anulada_por = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="notas_anuladas")

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

    # CNPJ da Zanattex nessa NF (emit na saída, dest na entrada) — cada CNPJ
    # é um centro de custo. Só dígitos; o rótulo (razão social + CNPJ
    # formatado) é montado em servicos.rotulos_centro_custo. Filtro de
    # Relatórios/Histórico/Saldo.
    centro_custo = models.CharField("Centro de custo", max_length=120, blank=True, db_index=True)

    valor_total = models.DecimalField("Valor total", max_digits=14, decimal_places=2, default=0)

    arquivo_origem = models.CharField("Arquivo de origem", max_length=255, blank=True)
    importado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    importado_em = models.DateTimeField(auto_now_add=True)
    xml_bruto = models.TextField("XML original", blank=True)

    # Fontes extras de referência à NF de entrada, usadas em cadeia pelo
    # casamento automático quando o infAdProd do item não basta sozinho (ver
    # fiscal/referencia.py e fiscal/matching.py). infCpl é da nota inteira
    # (não por item); ref_nfe guarda 0+ chaves de acesso (uma por linha).
    inf_cpl = models.TextField("Informações complementares", blank=True)
    ref_nfe = models.TextField(
        "NF-e referenciadas", blank=True,
        help_text="Chaves de acesso (44 dígitos) formalmente referenciadas no XML, uma por linha.")

    class CancelamentoOrigem(models.TextChoices):
        """Distingue os dois jeitos de uma nota virar CANCELADA (ver
        fiscal/sefaz.py e fiscal/importador.py): IMPORTACAO nunca teve saldo
        de verdade (detectado no ato do upload, resolvido sozinho, sem
        pendência); POS_IMPORTACAO já estava contando no saldo quando o
        SEFAZ acusou — precisa de revisão humana (fila de Pendências,
        motivo CANCELADA_SEFAZ)."""
        IMPORTACAO = "IMPORTACAO", "Detectado no ato da importação"
        POS_IMPORTACAO = "POS_IMPORTACAO", "Detectado após a importação (checagem periódica)"

    # Consulta ao SEFAZ (fiscal/sefaz.py) — última vez que essa nota foi
    # verificada (import, checagem periódica ou botão manual). É o campo que
    # a checagem periódica usa pra escolher "mais antiga primeiro", já que
    # não há filtro de janela de dias (ver plano em memory/sefaz-cancelamento-plano.md).
    situacao_sefaz_verificada_em = models.DateTimeField(
        "Verificada na SEFAZ em", null=True, blank=True)
    cancelamento_detectado_em = models.DateTimeField(null=True, blank=True)
    protocolo_cancelamento = models.CharField(max_length=20, blank=True)
    motivo_cancelamento = models.CharField(max_length=200, blank=True)
    cancelamento_origem = models.CharField(
        max_length=20, choices=CancelamentoOrigem.choices, blank=True)
    # Só True pro caso POS_IMPORTACAO, enquanto ninguém decidiu ainda (ver
    # fiscal/views.py::resolver_pendencia) — nunca chega a True no caso
    # IMPORTACAO (resolvido sozinho, sem entrar na fila).
    cancelamento_revisao_pendente = models.BooleanField(default=False)
    cancelamento_revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+")
    cancelamento_revisado_em = models.DateTimeField(null=True, blank=True)

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

    @property
    def centro_custo_nome(self) -> str:
        """Razão social do lado Zanattex da nota (o dono do centro_custo)."""
        return self.emit_nome if self.tipo == self.Tipo.SAIDA else self.dest_nome

    @property
    def centro_custo_rotulo(self) -> str:
        cnpj = formatar_cnpj(self.centro_custo)
        return f"{self.centro_custo_nome} — {cnpj}" if self.centro_custo_nome else cnpj

    @property
    def ref_nfe_lista(self) -> list[str]:
        return [linha.strip() for linha in self.ref_nfe.splitlines() if linha.strip()]


class NotaFiscalItem(models.Model):
    """Um item (produto) dentro de uma NF. `saldo_atual` só faz sentido em
    itens de ENTRADA — é o saldo denormalizado (não somado on-the-fly a
    cada tela) que mantém os relatórios rápidos mesmo com dezenas de
    milhares de linhas de histórico. Inicializado com `q_com` na entrada,
    decrementado transacionalmente a cada baixa (ver fiscal/matching.py)."""

    class TipoRetorno(models.TextChoices):
        DEVOLUCAO_INSUMO = "DEVOLUCAO_INSUMO", "Tecido usado/perdido na industrialização (baixa automática)"
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
    # Decisão manual de tirar este item do controle de saldo (ver
    # fiscal/views.py::resolver_pendencia, ação "excluir do controle de
    # saldo") sem afirmar que a NF foi cancelada na SEFAZ. `saldo_atual` fica
    # None igual a um item fora do escopo controlado (eh_ncm_controlado) —
    # este campo é só o que faz `matching.recalcular_baixas` não reatar o
    # saldo no próximo recálculo (ele decide `saldo_atual` só pelo NCM, que
    # não muda com a exclusão).
    excluido_manualmente = models.BooleanField(default=False)

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
    # Qual critério da cascata casou o item (NCM, código do produto,
    # associação aprendida...) ou "Resolução manual" quando veio de
    # views.py::resolver_pendencia — mostrado na coluna "Vínculo" do
    # Histórico (ver fiscal/matching.py::encontrar_entrada_candidata).
    criterio = models.CharField(max_length=60, blank=True)
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
    cadastrado — a importação inteira daquele arquivo para antes disso).

    Motivos alinhados com os "tipos de divergência" do protótipo do fiscal —
    `AGUARDANDO_ENTRADA` é o único "leve" (a referência foi encontrada, só
    falta a entrada ser importada; resolve sozinho quando isso acontece, ver
    fiscal/matching.py::reprocessar_pendencias_aguardando), os demais são
    problemas de verdade que precisam de decisão manual."""

    class Motivo(models.TextChoices):
        AGUARDANDO_ENTRADA = "AGUARDANDO_ENTRADA", "Aguardando a NF de entrada citada ser importada"
        SEM_REFERENCIA = "SEM_REFERENCIA", "Nenhuma referência à NF de entrada encontrada no XML"
        REF_NAO_IDENTIFICADA = "REF_NAO_IDENTIFICADA", "Cita uma NF, mas o número não foi identificado com segurança"
        MULTIPLAS_NF = "MULTIPLAS_NF", "Mais de uma NF de entrada citada na referência"
        MULTIPLOS_PRODUTOS = "MULTIPLOS_PRODUTOS", "Mais de um item da entrada é candidato"
        PRODUTO_SEM_CORRESPONDENTE = "PRODUTO_SEM_CORRESPONDENTE", "Nenhum item da entrada corresponde"
        UNIDADE_INCOMPATIVEL = "UNIDADE_INCOMPATIVEL", "Unidade da saída incompatível com a da entrada"
        QUANTIDADE_EXCEDIDA = "QUANTIDADE_EXCEDIDA", "Devolução maior que o saldo disponível"
        POSSIVEL_DUPLICIDADE = "POSSIVEL_DUPLICIDADE", "Possível NF duplicada (confira se uma foi cancelada)"
        ESTORNO_NAO_CONFERE = "ESTORNO_NAO_CONFERE", "NF de estorno sem nota anulada correspondente"
        TECIDO_NAO_RECONHECIDO = "TECIDO_NAO_RECONHECIDO", "Cita uma NF de entrada válida, mas o item não foi reconhecido como tecido"
        CNPJ_SEM_CLIENTE = "CNPJ_SEM_CLIENTE", "CNPJ do XML sem Cliente cadastrado"
        CANCELADA_SEFAZ = "CANCELADA_SEFAZ", "SEFAZ reporta esta NF como cancelada (detectado após a importação)"

    saida_item = models.ForeignKey(
        NotaFiscalItem, on_delete=models.CASCADE, related_name="pendencias", null=True, blank=True)
    # Só preenchido pro motivo CANCELADA_SEFAZ — essa pendência é sobre a NF
    # inteira (entrada ou saída) que o SEFAZ reportou cancelada, não sobre um
    # saida_item como os demais motivos (ver fiscal/sefaz_servico.py e
    # fiscal/views.py::resolver_pendencia).
    nota_fiscal = models.ForeignKey(
        NotaFiscal, on_delete=models.CASCADE, related_name="pendencias_cancelamento",
        null=True, blank=True)
    motivo = models.CharField(max_length=30, choices=Motivo.choices)
    detalhe = models.TextField("Detalhe", blank=True)
    # Itens da NF de entrada afetados, quando se sabe quais são: o item
    # excedido, o de unidade incompatível, os candidatos empatados. É o que
    # marca o status no Histórico só no item certo — o resto da NF segue
    # com o próprio status (o saldo de um item pode ser baixado depois,
    # numa saída só dele). Vazio quando nenhum item foi identificado.
    itens_entrada = models.ManyToManyField(
        NotaFiscalItem, blank=True, related_name="pendencias_entrada")

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


class AssociacaoProduto(models.Model):
    """Memória de resolução manual: quando alguém escolhe explicitamente
    "lembrar essa correspondência" ao resolver uma pendência (ver
    views.py::resolver_pendencia), da próxima vez que aparecer esse mesmo
    código de produto numa saída desse cliente, o casamento automático já
    sabe pra qual código de produto de entrada ele corresponde — sem abrir
    pendência de novo. Não é automático em toda resolução manual (só quando
    marcado), do mesmo jeito que no protótipo original."""
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="associacoes_produto")
    cprod_saida = models.CharField("Código do produto na saída", max_length=60)
    cprod_entrada = models.CharField("Código do produto na entrada", max_length=60)
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cliente", "cprod_saida"], name="fiscal_associacao_unica_por_cliente_produto"),
        ]
        verbose_name = "Associação de produto aprendida"
        verbose_name_plural = "Associações de produto aprendidas"

    def __str__(self) -> str:
        return f"{self.cliente}: {self.cprod_saida} → {self.cprod_entrada}"
