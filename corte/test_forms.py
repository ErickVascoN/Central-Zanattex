"""Fase 4 — gramatura/baby/plástico/tubo viraram campos reais só pra Manta
no formulário de registro de corte."""
from __future__ import annotations

from django.test import TestCase

from contas.models import UnidadeCorte

from .forms import RegistroCorteForm


class CamposDeMaterialPorUnidadeTests(TestCase):
    def test_manta_tem_os_4_campos(self):
        form = RegistroCorteForm(unidade=UnidadeCorte.IACANGA_MANTA)
        for campo in ("gramatura", "baby_kg", "plastico_kg", "tubo_kg"):
            self.assertIn(campo, form.fields)
            self.assertFalse(form.fields[campo].required)

    def test_lencol_nao_tem_nenhum(self):
        form = RegistroCorteForm(unidade=UnidadeCorte.LENCOL)
        for campo in ("gramatura", "baby_kg", "plastico_kg", "tubo_kg"):
            self.assertNotIn(campo, form.fields)

    def test_cortina_nao_tem_nenhum(self):
        form = RegistroCorteForm(unidade=UnidadeCorte.CORTINA)
        for campo in ("gramatura", "baby_kg", "plastico_kg", "tubo_kg"):
            self.assertNotIn(campo, form.fields)


class GramaturaValidacaoTests(TestCase):
    def _dados(self, gramatura):
        return {
            "data": "2026-09-01", "quantidade_pecas": "100", "kg_cortado": "50.00",
            "extra_cor": "Azul", "extra_estacao": "Mesa 1", "gramatura": gramatura,
        }

    def test_gramatura_zero_e_recusada(self):
        form = RegistroCorteForm(self._dados("0"), unidade=UnidadeCorte.IACANGA_MANTA)
        self.assertFalse(form.is_valid())
        self.assertIn("gramatura", form.errors)

    def test_gramatura_negativa_e_recusada(self):
        form = RegistroCorteForm(self._dados("-0.5"), unidade=UnidadeCorte.IACANGA_MANTA)
        self.assertFalse(form.is_valid())
        self.assertIn("gramatura", form.errors)

    def test_gramatura_positiva_passa(self):
        form = RegistroCorteForm(self._dados("0.55"), unidade=UnidadeCorte.IACANGA_MANTA)
        # Não valida o form inteiro aqui (estação/cor dependem de opções
        # cadastradas no banco) — só que gramatura em si não gera erro.
        form.is_valid()
        self.assertNotIn("gramatura", form.errors)

    def test_gramatura_vazia_passa_campo_opcional(self):
        dados = self._dados("")
        form = RegistroCorteForm(dados, unidade=UnidadeCorte.IACANGA_MANTA)
        form.is_valid()
        self.assertNotIn("gramatura", form.errors)
