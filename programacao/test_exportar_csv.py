import csv
import io
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.models import ProgramacaoCorte


class ExportarCsvTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("pcp", password="x")
        self.client.force_login(self.user)
        ProgramacaoCorte.objects.create(
            pedido="12345", cliente="CAMESA", produto="JOGO DE CAMA",
            categoria="Jogo de cama", saldo_carteira_snap=500, qnt_programada=500,
            semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
            unidade_corte=UnidadeCorte.values[0], destino_costura="MEGA BARIRI",
            prev_corte=date(2026, 8, 31), criado_por=self.user,
        )

    def _baixar(self):
        resp = self.client.get(reverse("programacao:exportar_csv"), {"semana": "2026-S36"})
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode("utf-8")

    def test_comeca_com_bom_para_o_excel_nao_ler_como_ansi(self):
        texto = self._baixar()
        self.assertTrue(texto.startswith("\ufeff"))
        self.assertIn("Previsão", texto)

    def test_separador_e_ponto_e_virgula_com_crlf(self):
        texto = self._baixar()
        self.assertIn("\r\n", texto)
        linhas = list(csv.reader(io.StringIO(texto.lstrip("\ufeff")), delimiter=";"))
        self.assertEqual(
            linhas[0],
            ["Local", "Pedido", "Cliente", "Unidade", "Produto", "Qtde", "Destino", "Previsão"],
        )
        # Oito colunas de verdade — era isto que colapsava numa só.
        self.assertEqual(len(linhas[1]), 8)
        self.assertEqual(linhas[1][1], "12345")
        self.assertEqual(linhas[1][5], "500")

    def test_previsao_leva_o_ano(self):
        linhas = list(csv.reader(io.StringIO(self._baixar().lstrip("\ufeff")), delimiter=";"))
        self.assertEqual(linhas[1][7], "31/08/2026")

    def test_sem_previsao_sai_vazio_em_vez_de_travessao(self):
        ProgramacaoCorte.objects.update(prev_corte=None)
        linhas = list(csv.reader(io.StringIO(self._baixar().lstrip("\ufeff")), delimiter=";"))
        self.assertEqual(linhas[1][7], "")
