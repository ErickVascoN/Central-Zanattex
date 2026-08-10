"""Testes da janela de comparação de períodos.

Aritmética de data é onde bug passa despercebido: o número sai plausível,
ninguém confere, e o dashboard passa meses mentindo. Os casos abaixo fixam
as três coisas que mais erram — tamanho da janela, virada de mês/ano e
29/02 — além da regra de não comparar contra base zero.
"""

from datetime import date

from django.test import SimpleTestCase

from .request_utils import janela_comparacao, variacao


class JanelaComparacaoTests(SimpleTestCase):

    def test_janela_anterior_tem_o_mesmo_tamanho(self):
        # 01–08/ago (8 dias) → 24–31/jul (8 dias), colado no início.
        de, ate = janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), "anterior")
        self.assertEqual((de, ate), (date(2026, 7, 24), date(2026, 7, 31)))
        self.assertEqual((ate - de).days, 7)

    def test_mes_corrente_incompleto_compara_com_pedaco_equivalente(self):
        # A ARMADILHA. No dia 8, a janela efetiva de agosto é 01–08. O
        # comparativo tem que ser um pedaço de 8 dias, nunca julho inteiro:
        # senão a queda aparente é de ~74% e o número vira pânico à toa.
        de, ate = janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), "anterior")
        self.assertEqual((ate - de).days + 1, 8)
        self.assertNotEqual(de, date(2026, 7, 1))

    def test_dia_unico_compara_com_o_dia_anterior(self):
        de, ate = janela_comparacao(date(2026, 8, 5), date(2026, 8, 5), "anterior")
        self.assertEqual((de, ate), (date(2026, 8, 4), date(2026, 8, 4)))

    def test_janela_anterior_atravessa_virada_de_ano(self):
        de, ate = janela_comparacao(date(2026, 1, 1), date(2026, 1, 5), "anterior")
        self.assertEqual((de, ate), (date(2025, 12, 27), date(2025, 12, 31)))

    def test_mes_fechado_compara_com_o_mes_anterior_inteiro(self):
        # Julho inteiro (31 dias) → os 31 dias anteriores. Não é "junho"
        # (30 dias) — é uma janela de mesmo tamanho, que é o contrato.
        de, ate = janela_comparacao(date(2026, 7, 1), date(2026, 7, 31), "anterior")
        self.assertEqual((ate - de).days + 1, 31)
        self.assertEqual(ate, date(2026, 6, 30))

    def test_ano_passado_mantem_as_mesmas_datas(self):
        de, ate = janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), "ano_passado")
        self.assertEqual((de, ate), (date(2025, 8, 1), date(2025, 8, 8)))

    def test_29_de_fevereiro_nao_derruba_o_dashboard(self):
        # 2024 é bissexto, 2023 não. `date.replace(year=...)` estoura aqui —
        # e um ValueError numa view derruba a página inteira.
        de, ate = janela_comparacao(date(2024, 2, 28), date(2024, 2, 29), "ano_passado")
        self.assertEqual((de, ate), (date(2023, 2, 28), date(2023, 2, 28)))

    def test_modo_desligado_nao_produz_janela(self):
        self.assertEqual(janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), ""),
                         (None, None))

    def test_modo_invalido_nao_produz_janela(self):
        self.assertEqual(
            janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), "trimestre"),
            (None, None))

    def test_sem_periodo_nao_produz_janela(self):
        self.assertEqual(janela_comparacao(None, None, "anterior"), (None, None))


class VariacaoTests(SimpleTestCase):

    def test_alta_e_queda(self):
        self.assertEqual(variacao(110, 100)["pct"], 10.0)
        self.assertEqual(variacao(110, 100)["direcao"], "up")
        self.assertEqual(variacao(80, 100)["pct"], -20.0)
        self.assertEqual(variacao(80, 100)["direcao"], "down")

    def test_sem_mudanca(self):
        self.assertEqual(variacao(100, 100)["direcao"], "flat")

    def test_base_zero_nao_gera_selo(self):
        # Janela anterior sem produção não significa "cresceu infinito" —
        # significa "não dá pra comparar". Selo nenhum é mais honesto.
        self.assertIsNone(variacao(500, 0))

    def test_atual_zero_contra_base_real_e_queda_total(self):
        self.assertEqual(variacao(0, 400)["pct"], -100.0)
