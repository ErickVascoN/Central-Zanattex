from __future__ import annotations

from django import forms

from corte.models import ProgramacaoCorte

from .models import EnvioProducao, FechamentoOP, RegistroProducao, RetornoProducao, tipo_os_sugerido


class EnvioProducaoForm(forms.ModelForm):
    """`programacao=` é opcional só pra não quebrar chamada sem contexto (o
    admin, por exemplo): quando vem, o tipo de OS já nasce escolhido a partir
    do destino de costura da OP."""

    class Meta:
        model = EnvioProducao
        fields = ["data", "tipo", "numero", "destino", "quantidade_pecas", "observacao"]
        widgets = {
            # format explícito: sem ele o Django localiza pra dd/mm/aaaa (pt-br)
            # e o <input type="date"> descarta o valor, deixando o campo vazio.
            "data": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "field-input"}),
            "tipo": forms.Select(attrs={"class": "field-input"}),
            "numero": forms.TextInput(attrs={"class": "field-input", "placeholder": "nº no ERP"}),
            "quantidade_pecas": forms.NumberInput(attrs={"class": "field-input"}),
            "observacao": forms.TextInput(attrs={"class": "field-input"}),
        }

    def __init__(self, *args, programacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._programacao = programacao
        if programacao is not None and not self.initial.get("tipo"):
            self.initial["tipo"] = tipo_os_sugerido(programacao.destino_costura)
        # Destino deixa de ser texto livre: `Prestador.nome` só pode ser um
        # nome desta mesma lista (ver controle_op.models.opcoes_prestador), e
        # um destino digitado diferente faria o link do prestador nunca achar
        # a OP — sem quebrar nada, só nunca aparecendo. Select fecha a porta.
        self.fields["destino"] = forms.ChoiceField(
            label=self.fields["destino"].label,
            choices=self._opcoes_destino(programacao),
            widget=forms.Select(attrs={"class": "field-input"}),
        )

    @staticmethod
    def _opcoes_destino(programacao) -> list[tuple[str, str]]:
        from programacao.forms import opcoes_destino_costura

        nomes = list(opcoes_destino_costura())
        # O destino da própria OP e os que já receberam envio entram mesmo se
        # tiverem saído da planilha — senão a tela recusaria um valor que ela
        # mesma gravou (facção desativada, OP antiga).
        extras = []
        if programacao is not None:
            extras.append(programacao.destino_costura)
            extras.extend(_destinos_da_op(programacao))
        for nome in extras:
            if nome and nome not in nomes:
                nomes.append(nome)
        return [(nome, nome) for nome in nomes]

    def clean_numero(self) -> str:
        """Espaço em volta e caixa baixa fariam "1234 " e "1234" passarem
        como OS diferentes — e a unicidade é o ponto do campo."""
        return (self.cleaned_data.get("numero") or "").strip().upper()

    def clean(self):
        """Não dá pra mandar pra facção peça que não saiu da mesa de corte.
        Diferente das outras conferências da tela, esta bloqueia: o caminho
        pra sair daqui é lançar o corte que faltou — e é justamente isso
        que a trava força a acontecer, em vez de deixar a OP seguir com
        enviado maior que cortado e o Balanço de material mentir depois."""
        dados = super().clean()
        quantidade = dados.get("quantidade_pecas") or 0
        if self._programacao is None or quantidade <= 0:
            return dados

        from corte.aproveitamento import calcular_aproveitamento

        cortado = calcular_aproveitamento(self._programacao).cortado_pecas
        ja_enviado = sum(
            e.quantidade_pecas for e in self._programacao.envios_producao.all())
        if ja_enviado + quantidade > cortado:
            disponivel = max(cortado - ja_enviado, 0)
            raise forms.ValidationError(
                f"Só há {disponivel} pçs cortadas disponíveis pra enviar "
                f"({cortado} cortadas − {ja_enviado} já enviadas). Se o corte foi "
                "maior do que está lançado, registre o corte que faltou primeiro.")
        return dados


def _destinos_da_op(programacao) -> list[str]:
    """Prestadores distintos que já receberam envio desta OP, em ordem de
    lançamento — dividir OP entre prestadores é comum, não exceção."""
    if programacao is None:
        return []
    vistos: list[str] = []
    for destino in programacao.envios_producao.order_by("data").values_list("destino", flat=True):
        if destino and destino not in vistos:
            vistos.append(destino)
    return vistos


class RegistroProducaoForm(forms.ModelForm):
    """`programacao=` opcional (mesmo padrão do `EnvioProducaoForm`) — só
    quando vem é que dá pra avisar sobre envio/produção fora de ordem, e pra
    decidir se `destino` precisa ser perguntado (ver `_destinos_da_op`)."""

    class Meta:
        model = RegistroProducao
        fields = ["data", "destino", "quantidade_pecas", "qualidade_segunda_pecas",
                  "retalho_kg", "observacao"]
        widgets = {
            # format explícito: sem ele o Django localiza pra dd/mm/aaaa (pt-br)
            # e o <input type="date"> descarta o valor, deixando o campo vazio.
            "data": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "field-input"}),
            "quantidade_pecas": forms.NumberInput(attrs={"class": "field-input", "min": "0"}),
            "qualidade_segunda_pecas": forms.NumberInput(attrs={"class": "field-input", "min": "0"}),
            "retalho_kg": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "observacao": forms.TextInput(attrs={"class": "field-input"}),
        }

    def __init__(self, *args, programacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._programacao = programacao
        self._destinos = _destinos_da_op(programacao)
        if len(self._destinos) <= 1:
            # Sem ambiguidade (0 ou 1 prestador até agora) — nem mostra o
            # campo; `save()` preenche sozinho quando há exatamente 1.
            del self.fields["destino"]
        else:
            self.fields["destino"] = forms.ChoiceField(
                label="Prestador",
                choices=[("", "Selecione o prestador…")] + [(d, d) for d in self._destinos],
                widget=forms.Select(attrs={"class": "field-input"}))

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.destino and len(self._destinos) == 1:
            instance.destino = self._destinos[0]
        if commit:
            instance.save()
        return instance

    def clean(self):
        """Barra o que não fecha com a etapa anterior. Um dia com 0 de 1ª e
        0 de 2ª não é apontamento, é linha vazia. E apontar acima do que foi
        enviado (ou sem envio nenhum) faz a OP afirmar que a facção produziu
        peça que nunca saiu daqui — o caminho certo é lançar o corte e o
        envio que faltaram, e é isso que a trava força.

        Vale pra QUEM LANÇA POR DENTRO, que tem como corrigir o corte na
        hora. O link do prestador (RegistroProducaoPrestadorForm) continua
        aceitando de propósito: a facção não tem como lançar corte nem
        esperar alguém lançar, então ali o excesso entra e vira pendência na
        ficha da OP, pra Zanattex resolver."""
        dados = super().clean()
        primeira = dados.get("quantidade_pecas") or 0
        segunda = dados.get("qualidade_segunda_pecas") or 0
        if primeira + segunda <= 0:
            raise forms.ValidationError(
                "Lance ao menos uma peça (1ª ou 2ª qualidade) neste apontamento.")

        if self._programacao is not None:
            from .producao import producao_por_op

            enviado = sum(e.quantidade_pecas for e in self._programacao.envios_producao.all())
            if enviado <= 0:
                raise forms.ValidationError(
                    "Ainda não há envio lançado pra esta OP — registre a OS antes de "
                    "apontar produção, senão a peça aparece produzida sem ter saído daqui.")
            acumulada = producao_por_op(self._programacao, enviado_pecas=enviado)
            novo_total = acumulada.produzido_total + primeira + segunda
            if novo_total > enviado:
                disponivel = max(enviado - acumulada.produzido_total, 0)
                raise forms.ValidationError(
                    f"Só há {disponivel} pçs por apontar nesta OP "
                    f"({enviado} enviadas − {acumulada.produzido_total} já apontadas). "
                    "Se a facção produziu mais do que está lançado, registre o corte e "
                    "o envio que faltaram primeiro.")
        return dados


class RetornoProducaoForm(forms.ModelForm):
    """`programacao=` opcional (mesmo padrão do `EnvioProducaoForm`) — só
    quando vem é que dá pra avisar sobre retorno acima do que foi apontado,
    e pra decidir se `destino` precisa ser perguntado (ver `_destinos_da_op`)."""

    class Meta:
        model = RetornoProducao
        fields = ["data", "destino", "quantidade_pecas", "retalho_kg", "observacao"]
        widgets = {
            # format explícito: sem ele o Django localiza pra dd/mm/aaaa (pt-br)
            # e o <input type="date"> descarta o valor, deixando o campo vazio.
            "data": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "field-input"}),
            "quantidade_pecas": forms.NumberInput(attrs={"class": "field-input"}),
            "retalho_kg": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "observacao": forms.TextInput(attrs={"class": "field-input"}),
        }

    def __init__(self, *args, programacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._programacao = programacao
        self._destinos = _destinos_da_op(programacao)
        if len(self._destinos) <= 1:
            del self.fields["destino"]
        else:
            self.fields["destino"] = forms.ChoiceField(
                label="Prestador",
                choices=[("", "Selecione o prestador…")] + [(d, d) for d in self._destinos],
                widget=forms.Select(attrs={"class": "field-input"}))

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.destino and len(self._destinos) == 1:
            instance.destino = self._destinos[0]
        if commit:
            instance.save()
        return instance

    def clean(self):
        """O retorno reconcilia contra o que a Produção apontou (ver
        controle_op/producao.py::calcular_producao). Voltar mais peça do que
        foi apontado é sinal de que o apontamento está atrasado — e como o
        retorno é o último elo antes do fechamento, deixar passar aqui
        significa fechar a OP com a conta furada. Trava, e o caminho é
        lançar o apontamento que faltou."""
        dados = super().clean()
        quantidade = dados.get("quantidade_pecas") or 0

        if self._programacao is not None and quantidade > 0:
            from .producao import producao_por_op

            retornado_atual = sum(
                r.quantidade_pecas for r in self._programacao.retornos_producao.all())
            acumulada = producao_por_op(self._programacao)
            novo_total_retornado = retornado_atual + quantidade
            if novo_total_retornado > acumulada.produzido_total:
                disponivel = max(acumulada.produzido_total - retornado_atual, 0)
                raise forms.ValidationError(
                    f"Só há {disponivel} pçs por retornar nesta OP "
                    f"({acumulada.produzido_total} apontadas − {retornado_atual} já "
                    "retornadas). Se voltou mais do que está apontado, registre o "
                    "apontamento de produção que faltou primeiro.")
        return dados


class FaturamentoParcialForm(forms.ModelForm):
    """Mini-form do painel de Faturamento — um total corrente de peças
    faturadas, não uma lista de NFs. Numa OP grande é normal faturar em
    partes conforme cada NF sai, bem antes de a OP terminar de ser
    produzida ou de ser baixada — este número existe só pra dar visibilidade
    de quanto já saiu, sem travar nada."""

    class Meta:
        model = FechamentoOP
        fields = ["quantidade_faturada"]
        widgets = {
            "quantidade_faturada": forms.NumberInput(attrs={"class": "field-input"}),
        }

    def __init__(self, *args, programacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._programacao = programacao

    def clean(self):
        """Aviso, não bloqueio: faturar acima do programado normalmente é
        digitação errada, mas não é papel desta tela travar o financeiro."""
        dados = super().clean()
        quantidade = dados.get("quantidade_faturada")
        if self._programacao is not None and quantidade is not None:
            if quantidade > self._programacao.qnt_programada:
                self.add_warning = (
                    f"{quantidade} pçs faturadas é mais do que as "
                    f"{self._programacao.qnt_programada} pçs programadas nesta OP — confira.")
        return dados


class RequisitadoForm(forms.ModelForm):
    """Mini-form do painel de Balanço — o número do requisitado (NF/PDF da
    OP) geralmente chega depois da OP já existir, então mora aqui, editável
    a qualquer momento, além do campo já existir também em
    `EditarProgramacaoForm` (programacao/forms.py) pra quem preferir
    corrigir por lá. Um só dos dois campos preenchido por vez: kg é Manta,
    metros é Lençol — não valida isso aqui, o Balanço já ignora o que não
    é da grandeza da unidade."""

    class Meta:
        model = ProgramacaoCorte
        fields = ["kg_requisitado", "metros_requisitado"]
        widgets = {
            "kg_requisitado": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "metros_requisitado": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
        }


class RegistroProducaoPrestadorForm(forms.ModelForm):
    """Mesmo model de `RegistroProducaoForm`, pro link do prestador (Fase
    2b) — sem login, então sem `criado_por` (usuário do sistema): pede o
    nome de quem preencheu em texto livre. `destino` não é campo aqui — a
    página já sabe qual prestador é (vem do token da URL), a view escreve
    isso sozinha; perguntar de novo só confundiria (e abriria brecha pra
    apontar em nome de outro prestador digitando um nome diferente)."""

    criado_por_nome = forms.CharField(
        label="Seu nome",
        widget=forms.TextInput(attrs={"class": "field-input", "placeholder": "Quem está preenchendo"}))

    class Meta:
        model = RegistroProducao
        fields = ["data", "quantidade_pecas", "qualidade_segunda_pecas", "retalho_kg", "observacao"]
        widgets = {
            "data": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "field-input"}),
            "quantidade_pecas": forms.NumberInput(attrs={"class": "field-input", "min": "0"}),
            "qualidade_segunda_pecas": forms.NumberInput(attrs={"class": "field-input", "min": "0"}),
            "retalho_kg": forms.NumberInput(attrs={"class": "field-input", "step": "0.01"}),
            "observacao": forms.TextInput(attrs={"class": "field-input"}),
        }

    def clean(self):
        """Mesmo bloqueio de RegistroProducaoForm — um dia com 0 de 1ª e 0
        de 2ª não é um apontamento, é uma linha vazia."""
        dados = super().clean()
        primeira = dados.get("quantidade_pecas") or 0
        segunda = dados.get("qualidade_segunda_pecas") or 0
        if primeira + segunda <= 0:
            raise forms.ValidationError(
                "Lance ao menos uma peça (1ª ou 2ª qualidade) neste apontamento.")
        return dados
