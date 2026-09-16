"""Comando de import Prestador+MetaPrestador a partir da planilha de metas
já sincronizada — idempotente, ignora facção fora da lista canônica."""
from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from .models import MetaPrestador, Prestador

_METAS_FAKE = [
    {"produto": "MANTA PRENSADA", "cliente": "", "faccao": "MEGA BARIRI",
     "meta_dia": 5000, "meta_mes": 110000, "meta_semana": 25000},
    {"produto": "MANTA", "cliente": "BURDAYS", "faccao": "GIATTEX",
     "meta_dia": 2000, "meta_mes": 44000, "meta_semana": 10000},
]


class ImportarMetasPrestadoresTests(TestCase):
    def _rodar(self, **opcoes):
        saida = StringIO()
        call_command("importar_metas_prestadores", stdout=saida, **opcoes)
        return saida.getvalue()

    @patch("controle_op.management.commands.importar_metas_prestadores.load_metas")
    @patch("controle_op.management.commands.importar_metas_prestadores.opcoes_prestador")
    def test_cria_prestador_e_meta(self, mock_opcoes, mock_metas):
        mock_opcoes.return_value = ["MEGA BARIRI", "GIATTEX"]
        mock_metas.return_value = _METAS_FAKE
        self._rodar()
        self.assertEqual(Prestador.objects.count(), 2)
        self.assertEqual(MetaPrestador.objects.count(), 2)
        meta = MetaPrestador.objects.get(prestador__nome="MEGA BARIRI")
        self.assertEqual(meta.produto, "MANTA PRENSADA")
        self.assertEqual(meta.cliente, "")
        self.assertEqual(meta.meta_pecas, 5000)
        meta_cliente = MetaPrestador.objects.get(prestador__nome="GIATTEX")
        self.assertEqual(meta_cliente.cliente, "BURDAYS")

    @patch("controle_op.management.commands.importar_metas_prestadores.load_metas")
    @patch("controle_op.management.commands.importar_metas_prestadores.opcoes_prestador")
    def test_rodar_de_novo_atualiza_em_vez_de_duplicar(self, mock_opcoes, mock_metas):
        mock_opcoes.return_value = ["MEGA BARIRI", "GIATTEX"]
        mock_metas.return_value = _METAS_FAKE
        self._rodar()
        atualizado = [dict(d) for d in _METAS_FAKE]
        atualizado[0]["meta_dia"] = 6000
        mock_metas.return_value = atualizado
        self._rodar()
        self.assertEqual(Prestador.objects.count(), 2)
        self.assertEqual(MetaPrestador.objects.count(), 2)
        self.assertEqual(
            MetaPrestador.objects.get(prestador__nome="MEGA BARIRI").meta_pecas, 6000)

    @patch("controle_op.management.commands.importar_metas_prestadores.load_metas")
    @patch("controle_op.management.commands.importar_metas_prestadores.opcoes_prestador")
    def test_faccao_fora_da_lista_canonica_e_ignorada(self, mock_opcoes, mock_metas):
        mock_opcoes.return_value = ["MEGA BARIRI"]  # GIATTEX não está aqui
        mock_metas.return_value = _METAS_FAKE
        saida = self._rodar()
        self.assertEqual(Prestador.objects.count(), 1)
        self.assertEqual(MetaPrestador.objects.count(), 1)
        self.assertIn("GIATTEX", saida)

    @patch("controle_op.management.commands.importar_metas_prestadores.load_metas")
    @patch("controle_op.management.commands.importar_metas_prestadores.opcoes_prestador")
    def test_dry_run_nao_grava_nada(self, mock_opcoes, mock_metas):
        mock_opcoes.return_value = ["MEGA BARIRI", "GIATTEX"]
        mock_metas.return_value = _METAS_FAKE
        self._rodar(dry_run=True)
        self.assertEqual(Prestador.objects.count(), 0)
        self.assertEqual(MetaPrestador.objects.count(), 0)

    @patch("controle_op.management.commands.importar_metas_prestadores.load_metas")
    def test_planilha_vazia_nao_quebra(self, mock_metas):
        mock_metas.return_value = []
        saida = self._rodar()
        self.assertIn("vazia", saida)
        self.assertEqual(Prestador.objects.count(), 0)

    @patch("controle_op.management.commands.importar_metas_prestadores.load_metas")
    @patch("controle_op.management.commands.importar_metas_prestadores.opcoes_prestador")
    def test_nao_recria_prestador_ja_existente(self, mock_opcoes, mock_metas):
        Prestador.objects.create(nome="MEGA BARIRI", telefone="5514999998888")
        mock_opcoes.return_value = ["MEGA BARIRI", "GIATTEX"]
        mock_metas.return_value = _METAS_FAKE
        self._rodar()
        prestador = Prestador.objects.get(nome="MEGA BARIRI")
        # Telefone já cadastrado à mão não é sobrescrito pelo import.
        self.assertEqual(prestador.telefone, "5514999998888")
