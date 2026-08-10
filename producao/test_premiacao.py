"""Testes da Premiação por Produtividade (ANEXO I).

Este módulo calcula DINHEIRO que vai para a folha — um erro aqui paga bônus
errado para pessoas reais e ninguém percebe olhando o dashboard. Os testes
abaixo fixam as regras de negócio que hoje só existiam descritas nas
docstrings de `premiacao_servicos.py`, cada uma escrita de forma que a
versão ingênua da conta (a que alguém escreveria "arrumando" o código sem
conhecer o Anexo) faça o teste falhar.

Datas: agosto/2026 começa num SÁBADO (01/08 = sáb, 03/08 = seg, 08/08 = sáb).
"""

from datetime import date
from decimal import Decimal

import pandas as pd
from django.test import TestCase

from .models import RegraPremiacao
from . import premiacao_servicos as ps

Atividade = RegraPremiacao.Atividade

SEG = pd.Timestamp("2026-08-03")   # segunda
TER = pd.Timestamp("2026-08-04")   # terça
SAB = pd.Timestamp("2026-08-01")   # sábado


def _df_jogos(linhas: list[dict]) -> pd.DataFrame:
    """DataFrame no formato que `interno_loader` entrega para GGTTEX_JOGOS."""
    base = {"COLABORADOR": "ANA", "SETOR": "COSTURA RETA", "PRODUTO": "",
            "TAMANHO": "", "OBSERVACAO": "", "QUANTIDADE": 0, "DATA": SEG}
    return pd.DataFrame([{**base, **linha} for linha in linhas])


def _regra(atividade, **kwargs) -> RegraPremiacao:
    campos = {
        "unidade": "GGTTEX_JOGOS", "atividade": atividade,
        "valor_peca_excedente": Decimal("0.50"),
        "vigente_desde": date(2026, 1, 1),
    }
    campos.update(kwargs)
    return RegraPremiacao.objects.create(**campos)


class PremiacaoTestCase(TestCase):
    """Base que zera as regras vindas da migração de seed
    (`0002_seed_regras_premiacao`) antes de cada teste.

    O banco de teste nasce com as metas/tarifas REAIS em vigor, e elas
    venceriam as regras montadas aqui (a busca pega a de `vigente_desde`
    mais recente). Estes testes existem para fixar a LÓGICA do cálculo, não
    os valores vigentes — o negócio muda tarifa pelo admin quando quiser, e
    isso não pode quebrar a suíte.
    """

    def setUp(self):
        super().setUp()
        RegraPremiacao.objects.all().delete()


class ClassificarAtividadeTests(PremiacaoTestCase):
    """A planilha é preenchida à mão — a classificação precisa tolerar o que
    de fato chega, não o que deveria chegar."""

    def test_setor_mapeia_direto_para_atividade(self):
        df = _df_jogos([{"SETOR": "COSTURA CANTO"}])
        out = ps.classificar_atividade(df, "GGTTEX_JOGOS")
        self.assertEqual(out["ATIVIDADE"].iloc[0], Atividade.COSTURA_CANTO)

    def test_erro_de_digitacao_conhecido_ainda_classifica(self):
        # "COSTUR RETA" (sem o A) é digitação comum na planilha; se parar de
        # ser aceito, a produção do dia some do cálculo e a pessoa perde bônus.
        df = _df_jogos([{"SETOR": "COSTUR RETA"}])
        out = ps.classificar_atividade(df, "GGTTEX_JOGOS")
        self.assertEqual(out["ATIVIDADE"].iloc[0], Atividade.COSTURA_RETA)

    def test_setor_mesa_depende_do_produto(self):
        df = _df_jogos([{"SETOR": "MESA", "PRODUTO": "CASEADO"}])
        out = ps.classificar_atividade(df, "GGTTEX_JOGOS")
        self.assertEqual(out["ATIVIDADE"].iloc[0], Atividade.CASEADO)

    def test_setor_desconhecido_fica_sem_atividade(self):
        # Defeito, troca de etiqueta etc. — não é erro, só não premia.
        df = _df_jogos([{"SETOR": "RETRABALHO"}])
        out = ps.classificar_atividade(df, "GGTTEX_JOGOS")
        self.assertEqual(out["ATIVIDADE"].iloc[0], "")

    def test_fronha_junta_bainha_e_fechamento_no_mesmo_balde(self):
        # O Anexo dá uma única meta/taxa para "Bainha ou Fechamento" — separar
        # as duas criaria duas metas onde o Anexo prevê uma.
        df = pd.DataFrame([
            {"COLABORADOR": "BIA", "FUNCAO": "BAINHA", "QUANTIDADE": 10, "DATA": SEG},
            {"COLABORADOR": "BIA", "FUNCAO": "FECHA", "QUANTIDADE": 10, "DATA": SEG},
        ])
        out = ps.classificar_atividade(df, "GGTTEX_FRONHA")
        self.assertEqual(list(out["ATIVIDADE"]),
                         [Atividade.BAINHA_FECHAMENTO, Atividade.BAINHA_FECHAMENTO])


class MetaPorTamanhoTests(PremiacaoTestCase):
    """Costura de Canto/Elástico têm meta diferente por tamanho produzido."""

    def setUp(self):
        super().setUp()
        _regra(Atividade.COSTURA_CANTO, usa_tamanho=True, meta_solteiro=100,
               meta_casal=80, meta_queen=60, meta_tamanhos_mistos=70)

    def _meta_do_dia(self, tamanhos: list[str]) -> int:
        df = _df_jogos([
            {"SETOR": "COSTURA CANTO", "TAMANHO": t, "QUANTIDADE": 10}
            for t in tamanhos
        ])
        dias = ps._dias_do_periodo(df, "GGTTEX_JOGOS")
        return int(dias["META_DIA"].iloc[0])

    def test_um_unico_tamanho_no_dia_usa_a_meta_daquele_tamanho(self):
        self.assertEqual(self._meta_do_dia(["CASAL", "CASAL"]), 80)

    def test_mais_de_um_tamanho_no_mesmo_dia_usa_a_meta_mista(self):
        # Não é a média nem a maior — o Anexo prevê uma meta própria pra isso.
        self.assertEqual(self._meta_do_dia(["SOLTEIRO", "CASAL"]), 70)


class FechamentoDoPeriodoTests(PremiacaoTestCase):
    """O bônus é fechado no PERÍODO, não dia a dia — é a regra que mais muda
    o valor pago e a mais fácil de quebrar sem perceber."""

    def setUp(self):
        super().setUp()
        _regra(Atividade.COSTURA_RETA, meta_padrao=100)

    def test_dia_forte_compensa_dia_fraco_dentro_do_periodo(self):
        # 130 + 70 = 200 peças em 2 dias úteis, meta 100/dia → bate exatamente
        # a meta do período: excedente ZERO.
        # Se alguém trocar isso por uma conta diária, o dia de 130 geraria 30
        # de excedente (R$ 15,00) que o Anexo não prevê.
        df = _df_jogos([
            {"DATA": SEG, "QUANTIDADE": 130},
            {"DATA": TER, "QUANTIDADE": 70},
        ])
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=2)
        linha = calc.iloc[0]
        self.assertEqual(linha["PRODUZIDO"], 200)
        self.assertEqual(linha["META_TOTAL"], 200)
        self.assertEqual(linha["EXCEDENTE"], 0)
        self.assertEqual(linha["VALOR_BONUS"], 0)

    def test_excedente_do_periodo_paga_o_valor_por_peca(self):
        df = _df_jogos([
            {"DATA": SEG, "QUANTIDADE": 150},
            {"DATA": TER, "QUANTIDADE": 100},
        ])
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=2)
        linha = calc.iloc[0]
        self.assertEqual(linha["EXCEDENTE"], 50)          # 250 - 200
        self.assertEqual(linha["VALOR_BONUS"], 25.0)      # 50 × R$ 0,50

    def test_meta_do_periodo_usa_dias_uteis_do_calendario_nao_dias_lancados(self):
        # A pessoa lançou só 1 dia, mas o período tem 5 dias úteis: a meta é do
        # período inteiro (falta não abate meta), então 150 peças ficam LONGE
        # da meta de 500 — sem excedente.
        df = _df_jogos([{"DATA": SEG, "QUANTIDADE": 150}])
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=5)
        linha = calc.iloc[0]
        self.assertEqual(linha["META_TOTAL"], 500)
        self.assertEqual(linha["EXCEDENTE"], 0)


class SabadoTests(PremiacaoTestCase):
    """Sábado fica FORA da meta e é pago integralmente."""

    def setUp(self):
        super().setUp()
        _regra(Atividade.COSTURA_RETA, meta_padrao=100)

    def test_sabado_e_pago_mesmo_com_dias_uteis_abaixo_da_meta(self):
        # 80 + 80 = 160 contra meta de 200 → nenhum excedente nos dias úteis.
        # Ainda assim o sábado (50 peças) é pago inteiro: R$ 25,00.
        df = _df_jogos([
            {"DATA": SEG, "QUANTIDADE": 80},
            {"DATA": TER, "QUANTIDADE": 80},
            {"DATA": SAB, "QUANTIDADE": 50},
        ])
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=2)
        linha = calc.iloc[0]
        self.assertEqual(linha["EXCEDENTE"], 0)
        self.assertEqual(linha["VALOR_BONUS"], 0)
        self.assertEqual(linha["PRODUZIDO_SABADO"], 50)
        self.assertEqual(linha["VALOR_SABADO"], 25.0)

    def test_producao_de_sabado_nao_entra_no_excedente_dos_dias_uteis(self):
        # Se o sábado entrasse na conta da meta, 160 + 50 = 210 passaria dos
        # 200 e geraria excedente — exatamente o bug que a regra evita.
        df = _df_jogos([
            {"DATA": SEG, "QUANTIDADE": 80},
            {"DATA": TER, "QUANTIDADE": 80},
            {"DATA": SAB, "QUANTIDADE": 50},
        ])
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=2)
        linha = calc.iloc[0]
        self.assertEqual(linha["PRODUZIDO"], 160)         # só os dias úteis
        self.assertEqual(linha["DIAS_TRABALHADOS"], 2)
        self.assertEqual(linha["EXCEDENTE"], 0)


class RateioEntreAtividadesTests(PremiacaoTestCase):
    """Quem faz mais de uma função divide os MESMOS dias entre elas."""

    def setUp(self):
        super().setUp()
        _regra(Atividade.COSTURA_RETA, meta_padrao=100)
        _regra(Atividade.CASEADO, meta_padrao=100)

    def test_meta_e_rateada_pela_fatia_de_dias_de_cada_atividade(self):
        # Ana fez Costura Reta na segunda e Caseado na terça. Cada atividade
        # pegou metade dos dias → cada uma responde por metade da meta do
        # período (100), não pela meta inteira (200). Sem o rateio, a meta de
        # cada atividade dobraria e o bônus sumiria.
        df = _df_jogos([
            {"DATA": SEG, "SETOR": "COSTURA RETA", "QUANTIDADE": 150},
            {"DATA": TER, "SETOR": "MESA", "PRODUTO": "CASEADO", "QUANTIDADE": 150},
        ])
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=2)
        por_atividade = calc.set_index("ATIVIDADE")

        self.assertEqual(por_atividade.loc[Atividade.COSTURA_RETA, "META_TOTAL"], 100)
        self.assertEqual(por_atividade.loc[Atividade.COSTURA_RETA, "EXCEDENTE"], 50)
        self.assertEqual(por_atividade.loc[Atividade.CASEADO, "META_TOTAL"], 100)
        self.assertEqual(por_atividade.loc[Atividade.CASEADO, "EXCEDENTE"], 50)


class RegraVigenteTests(PremiacaoTestCase):
    """A meta/taxa muda por data — o período pode pegar duas regras."""

    def test_cada_dia_usa_a_regra_vigente_naquela_data(self):
        _regra(Atividade.COSTURA_RETA, meta_padrao=100,
               vigente_desde=date(2026, 1, 1), vigente_ate=date(2026, 8, 3))
        _regra(Atividade.COSTURA_RETA, meta_padrao=200,
               vigente_desde=date(2026, 8, 4))

        df = _df_jogos([
            {"DATA": SEG, "QUANTIDADE": 10},   # regra antiga: meta 100
            {"DATA": TER, "QUANTIDADE": 10},   # regra nova:   meta 200
        ])
        dias = ps._dias_do_periodo(df, "GGTTEX_JOGOS").sort_values("DATA")
        self.assertEqual(list(dias["META_DIA"]), [100, 200])

        # A meta do período usa a MÉDIA das metas diárias: (100 + 200) / 2.
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=2)
        self.assertEqual(calc.iloc[0]["META_MEDIA_DIA"], 150.0)
        self.assertEqual(calc.iloc[0]["META_TOTAL"], 300)

    def test_dia_sem_regra_vigente_fica_fora_do_calculo(self):
        # Produção anterior à vigência da primeira regra não pode ser premiada
        # com uma taxa que ainda não existia.
        _regra(Atividade.COSTURA_RETA, meta_padrao=100,
               vigente_desde=date(2026, 8, 4))
        df = _df_jogos([{"DATA": SEG, "QUANTIDADE": 500}])
        calc = ps.calcular_premiacao(df, "GGTTEX_JOGOS", dias_uteis_periodo=2)
        self.assertTrue(calc.empty)
