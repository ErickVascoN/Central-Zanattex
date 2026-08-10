"""Testes da janela de comparação de períodos.

Aritmética de data é onde bug passa despercebido: o número sai plausível,
ninguém confere, e o dashboard passa meses mentindo. Os casos abaixo fixam
as três coisas que mais erram — tamanho da janela, virada de mês/ano e
29/02 — além da regra de não comparar contra base zero.
"""

from datetime import date, timedelta

from django.test import SimpleTestCase

from .feriados import contar_dias_uteis
from .request_utils import janela_comparacao, variacao


def _uteis(de, ate):
    return contar_dias_uteis(de, ate)


class JanelaComparacaoTests(SimpleTestCase):

    def test_janela_anterior_tem_os_mesmos_dias_uteis(self):
        # 01–08/ago/26 (sáb a sáb) = 5 dias úteis → os 5 dias úteis anteriores,
        # que são 27–31/jul (seg a sex). Não é "os 8 dias corridos anteriores":
        # aqueles teriam só 3 dias úteis (ver o teste de regressão abaixo).
        de, ate = janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), "anterior")
        self.assertEqual((de, ate), (date(2026, 7, 27), date(2026, 7, 31)))
        self.assertEqual(_uteis(de, ate), _uteis(date(2026, 8, 1), date(2026, 8, 8)))

    def test_mes_corrente_incompleto_compara_com_pedaco_equivalente(self):
        # A ARMADILHA. No dia 8, a janela efetiva de agosto é 01–08. O
        # comparativo tem que ser um pedaço equivalente, nunca julho inteiro:
        # senão a queda aparente é de ~74% e o número vira pânico à toa.
        de, ate = janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), "anterior")
        self.assertEqual(_uteis(de, ate), 5)
        self.assertNotEqual(de, date(2026, 7, 1))

    def test_dia_unico_compara_com_o_dia_util_anterior(self):
        de, ate = janela_comparacao(date(2026, 8, 5), date(2026, 8, 5), "anterior")
        self.assertEqual((de, ate), (date(2026, 8, 4), date(2026, 8, 4)))

    def test_segunda_isolada_pula_o_fim_de_semana(self):
        # 03/08/26 é segunda: o dia útil anterior é a sexta 31/07, não o
        # domingo 02/08 (que não tem produção e zeraria a base).
        de, ate = janela_comparacao(date(2026, 8, 3), date(2026, 8, 3), "anterior")
        self.assertEqual((de, ate), (date(2026, 7, 31), date(2026, 7, 31)))

    def test_janela_anterior_atravessa_virada_de_ano(self):
        de, ate = janela_comparacao(date(2026, 1, 1), date(2026, 1, 5), "anterior")
        self.assertEqual(_uteis(de, ate), _uteis(date(2026, 1, 1), date(2026, 1, 5)))
        self.assertLess(ate, date(2026, 1, 1))

    def test_mes_fechado_compara_com_periodo_de_mesmo_esforco(self):
        de, ate = janela_comparacao(date(2026, 7, 1), date(2026, 7, 31), "anterior")
        self.assertEqual(_uteis(de, ate), _uteis(date(2026, 7, 1), date(2026, 7, 31)))
        self.assertEqual(ate, date(2026, 6, 30))

    def test_ano_passado_alinha_o_esforco_nao_o_calendario(self):
        # Manter as MESMAS datas é o que desalinha: o dia da semana anda 1–2
        # dias por ano. A janela termina na data equivalente e recua o quanto
        # for preciso pra ter o mesmo número de dias úteis.
        de, ate = janela_comparacao(date(2026, 8, 1), date(2026, 8, 8), "ano_passado")
        self.assertEqual(_uteis(de, ate), _uteis(date(2026, 8, 1), date(2026, 8, 8)))
        self.assertEqual(ate.year, 2025)

    def test_29_de_fevereiro_nao_derruba_o_dashboard(self):
        # 2024 é bissexto, 2023 não. `date.replace(year=...)` estoura aqui —
        # e um ValueError numa view derruba a página inteira.
        de, ate = janela_comparacao(date(2024, 2, 28), date(2024, 2, 29), "ano_passado")
        self.assertEqual(_uteis(de, ate), _uteis(date(2024, 2, 28), date(2024, 2, 29)))
        self.assertEqual(ate.year, 2023)

    def test_feriado_nao_conta_como_dia_de_trabalho(self):
        # 07/09/2026 (Independência) cai numa segunda. Uma janela seg–sex
        # dessa semana tem 4 dias úteis, e o comparativo tem que ter 4 também.
        de, ate = janela_comparacao(date(2026, 9, 7), date(2026, 9, 11), "anterior")
        self.assertEqual(_uteis(date(2026, 9, 7), date(2026, 9, 11)), 4)
        self.assertEqual(_uteis(de, ate), 4)

    def test_janela_sem_dia_util_nao_compara(self):
        # Um sábado isolado: não há esforço a comparar, e qualquer número
        # exibido seria invenção.
        self.assertEqual(
            janela_comparacao(date(2026, 8, 1), date(2026, 8, 2), "anterior"),
            (None, None))


class AlinhamentoExaustivoTests(SimpleTestCase):
    """A garantia que interessa é estrutural, não caso a caso: para QUALQUER
    janela, os dois lados têm o mesmo número de dias úteis. Antes disso valia
    em ~30% das janelas no modo "anterior" e ~49% no "ano passado"."""

    def test_todas_as_janelas_ficam_alinhadas(self):
        desalinhadas = []
        for modo in ("anterior", "ano_passado"):
            for off in range(0, 400):
                de = date(2025, 1, 1) + timedelta(days=off)
                for tam in (1, 2, 3, 5, 7, 8, 10, 14, 21, 30, 31, 45):
                    ate = de + timedelta(days=tam - 1)
                    n = _uteis(de, ate)
                    cde, cate = janela_comparacao(de, ate, modo)
                    if cde is None:
                        self.assertEqual(n, 0, f"{modo} {de}..{ate}: sem janela mas há {n} úteis")
                        continue
                    if _uteis(cde, cate) != n:
                        desalinhadas.append((modo, de, ate, n, cde, cate, _uteis(cde, cate)))
        self.assertEqual(desalinhadas[:5], [], f"{len(desalinhadas)} janelas desalinhadas")

    def test_janela_anterior_nunca_invade_a_atual(self):
        for off in range(0, 200):
            de = date(2025, 3, 1) + timedelta(days=off)
            ate = de + timedelta(days=6)
            cde, cate = janela_comparacao(de, ate, "anterior")
            if cde is not None:
                self.assertLess(cate, de, f"{de}..{ate}: comparação invadiu a janela atual")
                self.assertLessEqual(cde, cate)

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

    def test_carrega_os_dois_lados_da_conta(self):
        """O selo exibe os dois valores, não só a porcentagem: a base dele
        (só dias úteis) pode diferir do KPI logo acima (período inteiro), e aí
        a porcentagem sozinha parece não fechar — foi o que acontecia com
        "Dias 4" e "-40%", que era 3 vs 5."""
        v = variacao(110248, 234331)
        self.assertEqual(v["atual"], 110248)
        self.assertEqual(v["anterior"], 234331)


class SeloVariacaoTemplateTests(SimpleTestCase):
    """O que o selo mostra na tela."""

    def _render(self, v, label="27/07 a 31/07/2026 · dias úteis"):
        from django.template.loader import render_to_string
        return render_to_string("_partials/selo_variacao.html", {"v": v, "label": label})

    def test_mostra_os_dois_valores_e_a_porcentagem(self):
        html = self._render(variacao(110248, 234331))
        self.assertIn("-53.0%", html.replace(",", "."))
        self.assertIn("234.331", html)      # anterior
        self.assertIn("110.248", html)      # atual
        self.assertIn("dias úteis", html)

    def test_sem_base_nao_renderiza_nada(self):
        self.assertEqual(self._render(None).strip(), "")

    def test_queda_e_alta_mudam_o_indicador(self):
        self.assertIn("▼", self._render(variacao(80, 100)))
        self.assertIn("▲", self._render(variacao(120, 100)))
