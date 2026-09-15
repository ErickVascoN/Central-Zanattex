"""Tabelas do pós-corte — o pedaço do fluxo que fica entre "Corte Gerado" e
"Baixar Ordem de Produção" no diagrama de processos da Zanattex: definição de
onde a OP vai ser industrializada, envio das peças cortadas pro prestador
(interno ou externo), e o retorno (peças prontas + retalho gerado na
produção). `corte/models.py` já cobre a ponta de programação/corte; este
módulo cobre a ponta de produção — junto, os dois dão o rollup completo que
`controle_op/producao.py` e as telas deste app consomem.

A produção tem DUAS fontes convivendo de propósito: `RegistroProducao` (o
apontamento manual desta tabela, vinculado à OP de verdade) e a leitura ao
vivo da planilha de facções (`producao.faccao_loader.load_faccoes()`, casada
por cliente+produto+facção em `controle_op/producao.py::producao_diaria_auto()`).
O manual é quem vale pro fechamento; o automático fica ao lado como
referência, e só será aposentado depois de o apontamento provar, em uso real,
que fecha ponta a ponta contra os pedidos."""
from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models

from corte.models import ProgramacaoCorte
from integracao.normalize import normalize_text


DESTINO_INTERNO = "COSTURA INTERNA"


def _gerar_token() -> str:
    # 32 bytes = 256 bits de entropia, url-safe — inadivinhável na prática.
    # Não é sequencial nem derivado de nada previsível (id, nome, data).
    return secrets.token_urlsafe(32)


def opcoes_prestador() -> list[str]:
    """Mesma lista canônica de `programacao.forms.opcoes_destino_costura()`
    (que vem de `producao.faccao_loader.load_faccoes()`) — a MESMA fonte que
    já alimenta o `<select>` de destino_costura na Programação, sem
    "COSTURA INTERNA" (produção interna não tem link, é a própria Zanattex).
    `Prestador.nome` só pode ser um destes — travado no cadastro (admin +
    `Prestador.clean()`), não texto livre: um nome digitado diferente do
    usado em `EnvioProducao.destino` faria o link nunca achar nenhuma OP,
    silenciosamente (nada quebra, só nunca aparece nada — o pior tipo de
    bug, porque não avisa)."""
    from programacao.forms import opcoes_destino_costura
    return [n for n in opcoes_destino_costura() if n != DESTINO_INTERNO]


class Prestador(models.Model):
    """Facção/prestador externo — dono do link sem login da Fase 2b
    (`controle_op:prestador_lista`/`prestador_op`). O link é escopado pelo
    `token`: a página só lista/aceita apontamento pras OPs que já têm
    `EnvioProducao.destino == este prestador` — nunca mostra OP de outro
    prestador, mesmo que o link de alguém vaze.

    `nome` precisa ser um dos valores de `opcoes_prestador()` (ver ali o
    porquê) — validado tanto no form do admin (Select, não texto livre)
    quanto em `clean()`, pra nenhum outro caminho de criação (shell, script)
    driblar a trava. Gerenciado pelo Django admin, mesmo padrão de
    Cortador/EstacaoCorte (corte/models.py)."""

    nome = models.CharField(max_length=120, unique=True)
    telefone = models.CharField(
        "Telefone (WhatsApp)", max_length=20, blank=True,
        help_text="Só dígitos, com DDI+DDD (ex.: 5514999998888) — usado pro link de wa.me.")
    token = models.CharField(max_length=43, unique=True, default=_gerar_token, editable=False)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Prestador"
        verbose_name_plural = "Prestadores"

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.nome or self.nome in opcoes_prestador():
            return
        # Grandfathered: já estava salvo com esse nome antes de sair da
        # lista viva (facção que saiu da planilha) — reeditar outros campos
        # não pode travar por isso. Só barra ESCOLHER esse nome de novo (de
        # um registro novo, ou trocando pra ele num já existente).
        if self.pk and Prestador.objects.filter(pk=self.pk, nome=self.nome).exists():
            return
        raise ValidationError({
            "nome": "Esse nome não está na lista de facções/produção (mesma lista "
                    "usada no destino do Envio) — sem bater exatamente, o link nunca "
                    "vai achar nenhuma OP pra este prestador."
        })

    def __str__(self):
        return self.nome


class MetaPrestador(models.Model):
    """Meta de produção por prestador × produto (× cliente, quando a meta é
    específica de um cliente) — mesma granularidade da aba "METAS" da
    planilha "Produção Diária e Plano de Metas" (facção/produto/meta,
    cliente às vezes vazio), só que editável direto no admin, sem depender
    de mexer na planilha pra atualizar.

    Diferente da aba "BD PLANO DE METAS" que `metas/loader.py` já lê (essa
    tem Meta Mês + Produção Diária, PREVISTO/REALIZADO por mês) — a aba
    "METAS" é mais simples, um número só por facção/produto, sem grão de
    mês. Primeiro passo de tirar o Plano de Metas de cima da planilha, aos
    poucos — POR ORA SÓ CAPTURA O DADO: o dashboard de Metas × Realizado
    (app `metas`) continua lendo só a planilha sincronizada
    (`plano_metas`), que é sobrescrita inteira a cada sync
    (`integracao/db_sync.py::sync_dataframe`, `if_exists="replace"`) — não
    dava pra gravar aqui e ESPERAR que sobrevivesse. Ligar este model ao
    cálculo de Metas × Realizado é um passo seguinte, não este."""

    prestador = models.ForeignKey(Prestador, on_delete=models.CASCADE, related_name="metas")
    produto = models.CharField(max_length=120)
    # Em branco quando a meta vale pro prestador+produto em geral, sem
    # distinguir cliente — a aba real da planilha tem as duas situações
    # (a maioria das linhas sem cliente, algumas com).
    cliente = models.CharField(max_length=120, blank=True)
    meta_pecas = models.PositiveIntegerField("Meta (peças)")
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["prestador__nome", "produto", "cliente"]
        verbose_name = "Meta do prestador"
        verbose_name_plural = "Metas dos prestadores"
        constraints = [
            models.UniqueConstraint(
                fields=["prestador", "produto", "cliente"],
                name="meta_prestador_produto_cliente_unico",
            ),
        ]

    def __str__(self):
        sufixo = f" ({self.cliente})" if self.cliente else ""
        return f"{self.prestador} — {self.produto}{sufixo}"


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
    # Dividir uma OP entre prestadores é comum, não exceção — sem saber de
    # qual prestador veio este retorno, `saldo_por_prestador()` (controle_op/
    # producao.py) não teria como separar quanto cada um já devolveu. Em
    # branco só quando a OP inteira até agora foi pra um prestador só (aí é
    # implícito, ninguém precisa escolher) — ver RetornoProducaoForm.
    destino = models.CharField(
        "Prestador", max_length=120, blank=True,
        help_text="Só pede quando esta OP já foi enviada pra mais de um prestador.")
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


class RegistroProducao(models.Model):
    """Apontamento do que a facção produziu desta OP — espelha o
    `RegistroCorte` do corte: várias linhas por OP, uma por dia de trabalho,
    lançadas ao longo do processo e não só no fim.

    É o módulo que faltava entre Envio e Retorno. Sem ele o sistema só sabe
    "saiu" e "voltou", e não responde quanto ainda está parado na facção —
    a única fonte era o cruzamento aproximado por cliente+produto da planilha
    de facções (`producao.py::producao_diaria_auto`, que o próprio código já
    rotula como referência). Aqui o vínculo com a OP é real.

    **2ª qualidade nasce aqui**, não no Retorno: é na costura/acabamento que
    a peça é classificada. O Retorno só confirma que ela chegou fisicamente —
    não é lugar de inspecionar qualidade de novo. E 2ª qualidade conta como
    entregue (abate o saldo igual à peça boa), só fica rastreada à parte."""

    programacao = models.ForeignKey(
        ProgramacaoCorte, on_delete=models.PROTECT, related_name="registros_producao")
    data = models.DateField("Data da produção")
    # Mesma régua do RetornoProducao.destino — obrigatório só quando a OP já
    # tem envio pra mais de um prestador (ver RegistroProducaoForm).
    destino = models.CharField(
        "Prestador", max_length=120, blank=True,
        help_text="Só pede quando esta OP já foi enviada pra mais de um prestador.")
    quantidade_pecas = models.PositiveIntegerField("Peças produzidas (1ª qualidade)")
    qualidade_segunda_pecas = models.PositiveIntegerField("Peças de 2ª qualidade", default=0)
    retalho_kg = models.DecimalField(
        "Retalho da produção (kg)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Deixe vazio se não foi pesado — vazio não é zero, e o balanço "
                  "de material (Fase 4) trata ausência como dado faltante, não como "
                  "'não houve retalho'.")
    observacao = models.TextField(blank=True)

    class Origem(models.TextChoices):
        INTERNO = "INTERNO", "Lançado pela Zanattex"
        PRESTADOR = "PRESTADOR", "Lançado pelo prestador (link sem login)"

    origem = models.CharField(max_length=10, choices=Origem.choices, default=Origem.INTERNO)
    # `criado_por` fica nulo quando origem=PRESTADOR — quem preenche pelo
    # link não tem usuário do sistema. `criado_por_nome` guarda o nome em
    # texto livre digitado na hora (sem isso, um apontamento pelo link não
    # tem NINGUÉM associado — nem sistema nem humano identificável).
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="producoes_criadas",
        null=True, blank=True)
    criado_por_nome = models.CharField(
        "Nome de quem preencheu (prestador)", max_length=120, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criado_em"]
        verbose_name = "Apontamento de produção"
        verbose_name_plural = "Apontamentos de produção"
        constraints = [
            # Um apontamento sempre tem alguém por trás — ou um usuário do
            # sistema (INTERNO) ou um nome digitado no link (PRESTADOR).
            # Nunca os dois vazios ao mesmo tempo, nunca os dois preenchidos
            # (um apontamento não tem dois "autores" ao mesmo tempo).
            models.CheckConstraint(
                condition=(
                    models.Q(origem="INTERNO", criado_por__isnull=False, criado_por_nome="")
                    | (models.Q(origem="PRESTADOR", criado_por__isnull=True)
                       & ~models.Q(criado_por_nome=""))
                ),
                name="producao_autor_consistente_com_origem",
            ),
        ]

    def __str__(self):
        return f"{self.programacao} — {self.total_pecas} pçs em {self.data}"

    @property
    def total_pecas(self) -> int:
        """1ª + 2ª: as duas abatem o saldo do pedido, então o que a facção
        "produziu" naquele dia é a soma. A separação existe pra rastrear
        qualidade, não pra descontar a 2ª da entrega."""
        return self.quantidade_pecas + self.qualidade_segunda_pecas


class FechamentoOP(models.Model):
    """Os 2 fechamentos manuais da OP — Faturamento (confirmação de que o
    ERP já faturou) e a Baixa (encerramento de ponta a ponta, com o
    Balanço de material congelado num snapshot). Corte e Produção
    continuam sendo status CALCULADOS (ver corte/aproveitamento.py e
    controle_op/producao.py) — não duplicados aqui.

    A BAIXA vem antes do Faturamento na ordem real do processo — ver
    controle_op/baixa.py::baixar_op(), que é quem escreve os campos
    `op_baixada*`/`balanco_*`/`motivo_divergencia` abaixo. Não confirmar
    faturamento sem ter baixado é só aviso, não bloqueio (ver
    `confirmar_faturamento` em views.py) — numa OP grande é normal faturar
    em partes (várias NFs) enquanto ela ainda está em produção, bem antes
    de qualquer baixa. `quantidade_faturada` existe pra isso: um total
    corrente, atualizado conforme cada NF sai, independente da baixa."""

    programacao = models.OneToOneField(
        ProgramacaoCorte, on_delete=models.CASCADE, related_name="fechamento")
    faturamento_confirmado = models.BooleanField(default=False)
    faturamento_confirmado_em = models.DateTimeField(null=True, blank=True)
    faturamento_confirmado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="fechamentos_confirmados")
    # Total corrente de peças já faturadas no ERP, atualizado à mão conforme
    # cada NF sai — não uma lista de notas, só o número de hoje. Começa
    # genuinamente em 0 (nada faturado ainda é zero de verdade, não "não
    # medido"). Comparado contra programacao.qnt_programada pra achar o %.
    quantidade_faturada = models.PositiveIntegerField(default=0)
    observacao = models.TextField(blank=True)

    op_baixada = models.BooleanField(default=False)
    op_baixada_em = models.DateTimeField(null=True, blank=True)
    op_baixada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ops_baixadas")
    # A FOTO do Balanço no momento da baixa — não recalculado ao vivo depois.
    # Sem isso, um corte lançado por engano semana depois faria o balanço de
    # uma OP JÁ BAIXADA "mudar de ideia" silenciosamente. `balanco_snapshot`
    # é o dataclass BalancoOP inteiro (dataclasses.asdict), `balanco_status`
    # é só o status (duplicado aqui pra filtrar/exibir sem abrir o JSON).
    balanco_snapshot = models.JSONField(null=True, blank=True)
    balanco_status = models.CharField(max_length=20, blank=True)
    # Obrigatório sempre que o balanço não fechou exato (nem FECHADO nem
    # NAO_APLICAVEL) — a controladoria sempre pode baixar, nunca em
    # silêncio sobre uma divergência. Ver controle_op/baixa.py.
    motivo_divergencia = models.TextField(blank=True)

    class Meta:
        verbose_name = "Fechamento de OP"
        verbose_name_plural = "Fechamentos de OP"

    def __str__(self):
        return f"{self.programacao} — faturamento {'confirmado' if self.faturamento_confirmado else 'pendente'}"
