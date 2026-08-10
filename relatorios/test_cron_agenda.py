"""Testes da AGENDA do e-mail diário (`relatorios/cron.py`).

Erro aqui é silencioso nos dois sentidos: ou o e-mail sai num dia que não
devia (foi o que acontecia — sábado mandava a sexta ainda não lançada, e
domingo mandava o sábado), ou deixa de sair num dia que devia e ninguém
estranha, porque a ausência de e-mail não chama atenção.

Regra: só sai em dia útil, e sempre sobre o último dia útil anterior.
"""

from datetime import date
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from . import cron

# Semana de referência: 03/08/2026 (segunda) a 09/08/2026 (domingo).
SEG, TER, QUA, QUI, SEX, SAB, DOM = (date(2026, 8, d) for d in range(3, 10))


class UltimoDiaUtilTests(SimpleTestCase):

    def test_terca_a_sexta_olham_o_dia_anterior(self):
        for hoje, esperado in [(TER, SEG), (QUA, TER), (QUI, QUA), (SEX, QUI)]:
            with self.subTest(hoje=hoje):
                self.assertEqual(cron._ultimo_dia_util(hoje), esperado)

    def test_segunda_pula_o_fim_de_semana_e_pega_a_sexta(self):
        """O pedido central: a sexta sai na segunda, já lançada."""
        self.assertEqual(cron._ultimo_dia_util(date(2026, 8, 10)), SEX)

    def test_pula_feriado(self):
        """12/10 é feriado (N. Sra. Aparecida); em 2026 cai numa segunda,
        então a terça 13/10 tem que reportar a sexta 09/10."""
        self.assertEqual(cron._ultimo_dia_util(date(2026, 10, 13)), date(2026, 10, 9))

    def test_nunca_devolve_dia_nao_util(self):
        for dia in range(1, 29):
            d = date(2026, 8, dia)
            with self.subTest(d=d):
                anterior = cron._ultimo_dia_util(d)
                self.assertLess(anterior, d)
                self.assertLess(anterior.weekday(), 5)


class DatasDoEnvioTests(SimpleTestCase):
    """Numa segunda, sábado/domingo só entram se tiveram produção de fato —
    sábado tem hora extra de verdade (Iacanga e Lençol passam de 100 mil
    peças), mas sábado parado não pode virar seção vazia no e-mail."""

    SEGUNDA = date(2026, 8, 10)   # dia útil seguinte a SEX/SAB/DOM

    def _datas(self, com_producao=()):
        est = {"houve_producao": lambda d: d in com_producao}
        return cron._datas_do_envio(est, SEX, self.SEGUNDA)

    def test_fim_de_semana_parado_reporta_so_a_sexta(self):
        self.assertEqual(self._datas(), [SEX])

    def test_sabado_com_producao_entra_junto(self):
        self.assertEqual(self._datas(com_producao={SAB}), [SEX, SAB])

    def test_domingo_com_producao_tambem_entra(self):
        self.assertEqual(self._datas(com_producao={SAB, DOM}), [SEX, SAB, DOM])

    def test_dia_util_normal_nao_ganha_extras(self):
        """De terça a sexta não há dia pulado — nada além do dia anterior."""
        est = {"houve_producao": lambda d: True}
        self.assertEqual(cron._datas_do_envio(est, TER, QUA), [TER])

    def test_ordem_cronologica(self):
        self.assertEqual(self._datas(com_producao={SAB, DOM}), sorted(self._datas({SAB, DOM})))

    def test_estrategia_sem_checagem_nao_quebra(self):
        self.assertEqual(cron._datas_do_envio({}, SEX, self.SEGUNDA), [SEX])


class CorpoSecaoTests(SimpleTestCase):
    """Num dia não útil não havia lançamento esperado: chamar isso de
    "completo" seria mentira."""

    def test_dia_util_sem_pendencia_diz_completo(self):
        txt = cron._corpo_secao_texto("Corte", "07/08/2026", [], extra=False)
        self.assertIn("completo", txt)

    def test_dia_extra_nao_diz_completo(self):
        txt = cron._corpo_secao_texto("Corte · 08/08/2026", "08/08/2026", [], extra=True)
        self.assertNotIn("completo", txt.lower())
        self.assertIn("fora do dia útil", txt.lower())

    def test_html_do_dia_extra_tem_selo_proprio(self):
        html = cron._corpo_secao_html("Corte", "08/08/2026", [], extra=True)
        self.assertIn("Fora do dia útil", html)
        self.assertNotIn("Completo", html)

    def test_html_escapa_o_rotulo(self):
        html = cron._corpo_secao_html("<script>x</script>", "08/08/2026", [], extra=True)
        self.assertNotIn("<script>", html)


class CorpoComPresentesTests(SimpleTestCase):
    """O e-mail mostra quem lançou, não só quem falta: "faltam 10" não diz se
    é de 12 prestadores ou de 40. O denominador é a informação."""

    FALTAM = ["GIATTEX", "SUZANA"]
    LANCARAM = ["CAROL MENDES", "ZARO (LUIS)"]

    def test_texto_traz_placar_e_as_duas_listas(self):
        txt = cron._corpo_secao_texto("Produção", "07/08/2026", self.FALTAM,
                                      presentes=self.LANCARAM)
        self.assertIn("2 de 4 lançaram", txt)
        self.assertIn("Faltam: GIATTEX, SUZANA", txt)
        self.assertIn("Lançaram: CAROL MENDES, ZARO (LUIS)", txt)

    def test_html_traz_placar_e_as_duas_listas(self):
        html = cron._corpo_secao_html("Produção", "07/08/2026", self.FALTAM,
                                      presentes=self.LANCARAM)
        self.assertIn("2 de 4 lançaram", html)
        for nome in self.FALTAM + self.LANCARAM:
            self.assertIn(nome, html)
        self.assertIn("FALTAM".title(), html.title())
        self.assertIn("LANÇARAM".title(), html.title())

    def test_marca_acompanha_a_cor(self):
        """Cor sozinha não sobrevive a impressão P&B nem a daltonismo."""
        html = cron._corpo_secao_html("X", "07/08/2026", ["A"], presentes=["B"])
        self.assertIn("&#10007;", html)   # ✗ em quem falta
        self.assertIn("&#10003;", html)   # ✓ em quem lançou

    def test_completo_ainda_mostra_quem_lancou(self):
        html = cron._corpo_secao_html("Corte", "07/08/2026", [], presentes=["A", "B"])
        self.assertIn("Completo", html)
        self.assertIn("2 de 2 lançaram", html)
        self.assertIn("A", html)

    def test_sem_presentes_nao_quebra(self):
        """Compatível com quem chama sem a lista (e com o dia sem plano)."""
        txt = cron._corpo_secao_texto("Corte", "07/08/2026", ["A"])
        self.assertIn("Faltam: A", txt)
        self.assertNotIn("Lançaram", txt)
        self.assertIn("0 de 1", txt)

    def test_dia_fora_do_util_lista_quem_produziu_sem_cobrar_ninguem(self):
        html = cron._corpo_secao_html("Corte · 01/08", "01/08/2026", [],
                                      extra=True, presentes=["Lençol Arealva"])
        self.assertIn("Fora do dia útil", html)
        self.assertIn("Lençol Arealva", html)
        self.assertNotIn("Completo", html)
        self.assertNotIn("lançaram</span>", html)   # sem placar: nada era esperado

    def test_nome_com_html_e_escapado(self):
        html = cron._corpo_secao_html("X", "07/08/2026", ["<script>x</script>"],
                                      presentes=["<b>y</b>"])
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>y</b>", html)


class HandleAgendaTests(SimpleTestCase):
    """`handle` só deve trabalhar em dia útil."""

    def _chamar(self, hoje):
        req = RequestFactory().get("/relatorios/cron/")
        with patch.object(cron.timezone, "localdate", return_value=hoje), \
             patch.object(cron, "_ESTRATEGIAS", {}), \
             patch.object(cron, "_autorizado", return_value=True):
            return cron.handle(req)

    def test_sabado_e_domingo_nao_enviam(self):
        for dia in (SAB, DOM):
            with self.subTest(dia=dia):
                resp = self._chamar(dia)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(b"no-op", resp.content)
                self.assertIn(b"fim de semana", resp.content)

    def test_feriado_nao_envia(self):
        resp = self._chamar(date(2026, 10, 12))       # N. Sra. Aparecida
        self.assertIn(b"no-op", resp.content)

    def test_dia_util_segue_o_fluxo(self):
        """Com `_ESTRATEGIAS` vazio não há e-mail a mandar, mas o importante
        é que NÃO parou na barreira de fim de semana."""
        resp = self._chamar(QUA)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"fim de semana", resp.content)
