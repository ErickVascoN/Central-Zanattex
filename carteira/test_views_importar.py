"""Testes do fluxo de views da importação de Carteira via Excel
(upload -> resumo -> confirmar/cancelar).

`sync_dataframe` é sempre mockado aqui — ele grava na tabela sincronizada
`carteira_pedidos` via SQLAlchemy, fora do banco de teste do Django (usa
`settings.DATABASES['default']` direto, não a troca automática pro banco
de teste). Deixar rodar de verdade escreveria na base de desenvolvimento
real. `ImportacaoCarteira`, por ser um model Django normal, já usa o banco
de teste isolado sem precisar de nada especial."""
import io
import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import openpyxl
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import ImportacaoCarteira

_CABECALHO = [
    "Data Emissão", "Nota", "Pedido", "CFOP", "Destinatário", "Município",
    "Frete", "Vendedor", "Quantidade", "Valor Unit.", "Valor Total", "ST",
    "Cod. Prod.", "Venc. 1ª Fatura", "Descrição do Produto", "Centro de Custo",
]


def _xlsx_valido(n_linhas: int = 1) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([])
    ws.append(_CABECALHO)
    for i in range(n_linhas):
        ws.append([
            datetime(2026, 8, 24), None, 7000 + i, 5124, "VESTIS CONFECCOES LTDA",
            "AMERICANA-SP", "NI", 1, 10, 8.2, 82.0, 0, 18684,
            "12/30/1899", "CORTINA VENEZA 2,40X1,70 HAVAN", "GGTTEX",
        ])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _dir_uploads() -> Path:
    return Path(settings.BASE_DIR) / "cache" / "carteira_uploads"


class ImportarExcelViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        # Não deixa arquivo de teste esquecido em cache/carteira_uploads/
        # atrapalhando a próxima rodada de testes.
        for arquivo in _dir_uploads().glob("*.xlsx"):
            arquivo.unlink(missing_ok=True)

    def test_get_mostra_formulario_de_upload(self):
        resp = self.client.get(reverse("carteira:importar_excel"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Importar Carteira via Excel")

    def test_usuario_nao_admin_recebe_403(self):
        comum = get_user_model().objects.create_user("comum", password="x")
        self.client.force_login(comum)
        resp = self.client.get(reverse("carteira:importar_excel"))
        self.assertEqual(resp.status_code, 403)

    def test_upload_valido_mostra_resumo_sem_gravar_nada(self):
        arquivo = SimpleUploadedFile(
            "Carteira.xlsx", _xlsx_valido(3),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with patch("carteira.views.sync_dataframe") as mock_sync:
            resp = self.client.post(reverse("carteira:importar_excel"), {"arquivo": arquivo})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "3")  # linhas válidas
        mock_sync.assert_not_called()
        self.assertEqual(ImportacaoCarteira.objects.count(), 0)
        self.assertIn("carteira_import_token", self.client.session)

    def test_upload_extensao_invalida_e_recusado(self):
        arquivo = SimpleUploadedFile("carteira.pdf", b"nao e um excel", content_type="application/pdf")
        resp = self.client.post(reverse("carteira:importar_excel"), {"arquivo": arquivo})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("carteira_import_token", self.client.session)

    def test_upload_sem_linha_valida_nao_deixa_token_na_sessao(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["nada a ver"])
        buffer = io.BytesIO()
        wb.save(buffer)
        arquivo = SimpleUploadedFile("vazio.xlsx", buffer.getvalue())
        resp = self.client.post(reverse("carteira:importar_excel"), {"arquivo": arquivo})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("carteira_import_token", self.client.session)
        self.assertEqual(list(_dir_uploads().glob("*.xlsx")), [])  # não deixa arquivo órfão


class ConfirmarImportacaoViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        for arquivo in _dir_uploads().glob("*.xlsx"):
            arquivo.unlink(missing_ok=True)

    def _fazer_upload(self, n_linhas=3):
        arquivo = SimpleUploadedFile("Carteira.xlsx", _xlsx_valido(n_linhas))
        self.client.post(reverse("carteira:importar_excel"), {"arquivo": arquivo})
        return self.client.session["carteira_import_token"]

    def test_confirmar_grava_via_sync_dataframe_e_cria_auditoria(self):
        token = self._fazer_upload(n_linhas=4)
        with patch("carteira.views.sync_dataframe", return_value=True) as mock_sync:
            resp = self.client.post(
                reverse("carteira:confirmar_importacao"), {"token": token, "acao": "confirmar"})
        self.assertRedirects(resp, reverse("carteira:importar_excel"))
        mock_sync.assert_called_once()
        args, kwargs = mock_sync.call_args
        self.assertEqual(args[0], "carteira_pedidos")
        self.assertEqual(args[1], "carteira_pedidos")
        self.assertEqual(len(args[2]), 4)  # o DataFrame reprocessado tem as 4 linhas

        importacao = ImportacaoCarteira.objects.get()
        self.assertEqual(importacao.usuario, self.user)
        self.assertEqual(importacao.linhas_importadas, 4)
        self.assertEqual(importacao.nome_arquivo, "Carteira.xlsx")

    def test_confirmar_remove_o_arquivo_temporario_e_limpa_a_sessao(self):
        token = self._fazer_upload()
        caminho = _dir_uploads() / f"{token}.xlsx"
        self.assertTrue(caminho.exists())
        with patch("carteira.views.sync_dataframe", return_value=True):
            self.client.post(reverse("carteira:confirmar_importacao"), {"token": token, "acao": "confirmar"})
        self.assertFalse(caminho.exists())
        self.assertNotIn("carteira_import_token", self.client.session)

    def test_cancelar_nao_grava_nada_e_remove_o_arquivo(self):
        token = self._fazer_upload()
        caminho = _dir_uploads() / f"{token}.xlsx"
        with patch("carteira.views.sync_dataframe") as mock_sync:
            resp = self.client.post(
                reverse("carteira:confirmar_importacao"), {"token": token, "acao": "cancelar"})
        self.assertRedirects(resp, reverse("carteira:importar_excel"))
        mock_sync.assert_not_called()
        self.assertEqual(ImportacaoCarteira.objects.count(), 0)
        self.assertFalse(caminho.exists())

    def test_token_incorreto_e_recusado_sem_gravar(self):
        self._fazer_upload()
        with patch("carteira.views.sync_dataframe") as mock_sync:
            resp = self.client.post(
                reverse("carteira:confirmar_importacao"), {"token": "token-forjado", "acao": "confirmar"})
        self.assertRedirects(resp, reverse("carteira:importar_excel"))
        mock_sync.assert_not_called()
        self.assertEqual(ImportacaoCarteira.objects.count(), 0)

    def test_confirmar_sem_upload_previo_redireciona_sem_gravar(self):
        with patch("carteira.views.sync_dataframe") as mock_sync:
            resp = self.client.post(
                reverse("carteira:confirmar_importacao"), {"token": "qualquer", "acao": "confirmar"})
        self.assertRedirects(resp, reverse("carteira:importar_excel"))
        mock_sync.assert_not_called()

    def test_reprocessar_a_mesma_importacao_nao_duplica_auditoria(self):
        """Confirmar uma segunda vez com o mesmo token (ex.: duplo clique)
        não encontra mais o arquivo (já foi removido na primeira) e não
        cria uma segunda linha de auditoria."""
        token = self._fazer_upload()
        with patch("carteira.views.sync_dataframe", return_value=True):
            self.client.post(reverse("carteira:confirmar_importacao"), {"token": token, "acao": "confirmar"})
            self.client.post(reverse("carteira:confirmar_importacao"), {"token": token, "acao": "confirmar"})
        self.assertEqual(ImportacaoCarteira.objects.count(), 1)


class LimpezaDeUploadsOrfaosTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        for arquivo in _dir_uploads().glob("*.xlsx"):
            arquivo.unlink(missing_ok=True)

    def test_upload_antigo_abandonado_e_varrido_no_proximo_upload(self):
        _dir_uploads().mkdir(parents=True, exist_ok=True)
        orfao = _dir_uploads() / "orfao-de-teste.xlsx"
        orfao.write_bytes(_xlsx_valido(1))
        antigo = time.time() - 2 * 60 * 60  # 2h atrás — passa da janela de 1h
        os.utime(orfao, (antigo, antigo))

        arquivo = SimpleUploadedFile("novo.xlsx", _xlsx_valido(1))
        self.client.post(reverse("carteira:importar_excel"), {"arquivo": arquivo})

        self.assertFalse(orfao.exists())
