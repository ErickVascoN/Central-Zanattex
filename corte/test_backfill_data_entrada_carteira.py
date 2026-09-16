"""Backfill de `data_entrada_carteira` pras OPs criadas antes desse campo
existir — casa pela Carteira sincronizada hoje, pelo número do pedido."""
from __future__ import annotations

from datetime import date
from io import StringIO
from unittest.mock import patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from contas.models import UnidadeCorte
from .models import ProgramacaoCorte

_CARTEIRA_FAKE = pd.DataFrame({
    "PEDIDO": ["111", "111", "222"],
    "DATA": pd.to_datetime(["2026-01-10", "2026-01-15", "2026-02-01"]),
})


def _programacao(user, **campos) -> ProgramacaoCorte:
    dados = dict(
        pedido="111", cliente="CAMESA", produto="JOGO DE CAMA",
        categoria="Jogo de cama", saldo_carteira_snap=500, qnt_programada=500,
        semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
        unidade_corte=UnidadeCorte.values[0], destino_costura="MEGA BARIRI",
        criado_por=user, origem=ProgramacaoCorte.Origem.SISTEMA,
    )
    dados.update(campos)
    return ProgramacaoCorte.objects.create(**dados)


class BackfillDataEntradaCarteiraTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def _rodar(self, **opcoes):
        saida = StringIO()
        call_command("backfill_data_entrada_carteira", stdout=saida, **opcoes)
        return saida.getvalue()

    @patch("corte.management.commands.backfill_data_entrada_carteira.carregar_carteira")
    def test_preenche_com_a_data_mais_recente_do_pedido(self, mock_carteira):
        mock_carteira.return_value = _CARTEIRA_FAKE
        programacao = _programacao(self.user, pedido="111")
        self._rodar()
        programacao.refresh_from_db()
        # Pedido 111 tem duas linhas na carteira (10/01 e 15/01) — fica com a
        # mais recente, mesma régua da Nova Programação.
        self.assertEqual(programacao.data_entrada_carteira, date(2026, 1, 15))

    @patch("corte.management.commands.backfill_data_entrada_carteira.carregar_carteira")
    def test_nao_sobrescreve_data_ja_preenchida(self, mock_carteira):
        mock_carteira.return_value = _CARTEIRA_FAKE
        programacao = _programacao(
            self.user, pedido="111", data_entrada_carteira=date(2020, 1, 1))
        self._rodar()
        programacao.refresh_from_db()
        self.assertEqual(programacao.data_entrada_carteira, date(2020, 1, 1))

    @patch("corte.management.commands.backfill_data_entrada_carteira.carregar_carteira")
    def test_pedido_fora_da_carteira_fica_sem_data_e_avisa(self, mock_carteira):
        mock_carteira.return_value = _CARTEIRA_FAKE
        programacao = _programacao(self.user, pedido="999")
        saida = self._rodar()
        programacao.refresh_from_db()
        self.assertIsNone(programacao.data_entrada_carteira)
        self.assertIn("999", saida)

    @patch("corte.management.commands.backfill_data_entrada_carteira.carregar_carteira")
    def test_dry_run_nao_grava_nada(self, mock_carteira):
        mock_carteira.return_value = _CARTEIRA_FAKE
        programacao = _programacao(self.user, pedido="111")
        self._rodar(dry_run=True)
        programacao.refresh_from_db()
        self.assertIsNone(programacao.data_entrada_carteira)

    @patch("corte.management.commands.backfill_data_entrada_carteira.carregar_carteira")
    def test_carteira_vazia_nao_quebra(self, mock_carteira):
        mock_carteira.return_value = pd.DataFrame()
        saida = self._rodar()
        self.assertIn("vazia", saida)
