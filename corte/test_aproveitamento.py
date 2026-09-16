"""Fase 4 do mini-ERP da OP tocou fórmulas centrais aqui (média ponderada,
gramatura virando campo real, baby em kg direto) que não tinham teste
nenhum antes — cobrindo só o que mudou, não o módulo inteiro."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from contas.models import UnidadeCorte

from .aproveitamento import _gramatura_de, _media_ponderada, calcular_aproveitamento
from .models import ProgramacaoCorte, RegistroCorte


def _programacao(user, **campos) -> ProgramacaoCorte:
    dados = dict(
        pedido="99999", cliente="CAMESA", produto="MANTA CASAL",
        categoria="Manta", saldo_carteira_snap=100, qnt_programada=100,
        semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
        unidade_corte=UnidadeCorte.IACANGA_MANTA, destino_costura="MEGA BARIRI",
        criado_por=user, origem=ProgramacaoCorte.Origem.SISTEMA,
    )
    dados.update(campos)
    return ProgramacaoCorte.objects.create(**dados)


class MediaPonderadaTests(TestCase):
    def test_pesa_pelo_segundo_valor_da_tupla(self):
        # 500 peças a 0,5 + 10 peças a 0,6 — dominado pelo primeiro.
        media = _media_ponderada([(0.5, 500), (0.6, 10)])
        self.assertAlmostEqual(media, (0.5 * 500 + 0.6 * 10) / 510)

    def test_diferente_da_media_simples(self):
        """Prova que pesar importa de verdade — a simples daria 0,55."""
        media = _media_ponderada([(0.5, 500), (0.6, 10)])
        self.assertLess(media, 0.55)

    def test_lista_vazia_e_none(self):
        self.assertIsNone(_media_ponderada([]))

    def test_peso_total_zero_e_none(self):
        self.assertIsNone(_media_ponderada([(0.5, 0), (0.6, 0)]))


class GramaturaFallbackTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)

    def test_campo_real_tem_prioridade_sobre_extra(self):
        registro = RegistroCorte.objects.create(
            programacao=self.programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, gramatura=Decimal("0.5500"),
            extra={"gramatura": "0.10"}, criado_por=self.user,
        )
        self.assertAlmostEqual(_gramatura_de(registro), 0.55)

    def test_cai_pro_extra_quando_campo_real_esta_vazio(self):
        """Registro antigo, lançado antes da migração — sem backfill,
        continua lido do JSON até alguém editar."""
        registro = RegistroCorte.objects.create(
            programacao=self.programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100,
            extra={"gramatura": "0.45"}, criado_por=self.user,
        )
        self.assertAlmostEqual(_gramatura_de(registro), 0.45)

    def test_sem_nenhum_dos_dois_e_none(self):
        registro = RegistroCorte.objects.create(
            programacao=self.programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, criado_por=self.user,
        )
        self.assertIsNone(_gramatura_de(registro))


class KgFinalComBabyEmKgTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)

    def test_baby_soma_direto_sem_fator(self):
        """100 peças × 0,5 + 2 kg retalho + 1 kg baby = 53 kg — sem
        multiplicar baby por fator nenhum (era peças×0,1955 antes)."""
        RegistroCorte.objects.create(
            programacao=self.programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("60.00"),
            gramatura=Decimal("0.5000"), retalho_kg=Decimal("2.00"), baby_kg=Decimal("1.00"),
            criado_por=self.user,
        )
        resultado = calcular_aproveitamento(self.programacao)
        self.assertAlmostEqual(resultado.kg_final, 53.0)

    def test_gramatura_media_e_exposta_no_resultado(self):
        """V7: o painel/balanço precisam do valor, não só do kg_final
        derivado dele."""
        RegistroCorte.objects.create(
            programacao=self.programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("60.00"),
            gramatura=Decimal("0.5000"), criado_por=self.user,
        )
        resultado = calcular_aproveitamento(self.programacao)
        self.assertAlmostEqual(resultado.gramatura_media, 0.5)

    def test_totais_crus_expostos_para_o_balanco(self):
        """V7: kg_cortado_total/retalho_kg_total não podem mais sumir."""
        RegistroCorte.objects.create(
            programacao=self.programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("60.00"),
            retalho_kg=Decimal("2.00"), baby_kg=Decimal("1.00"),
            plastico_kg=Decimal("0.3"), tubo_kg=Decimal("0.2"),
            criado_por=self.user,
        )
        resultado = calcular_aproveitamento(self.programacao)
        self.assertAlmostEqual(resultado.kg_cortado_total, 60.0)
        self.assertAlmostEqual(resultado.retalho_kg_total, 2.0)
        self.assertAlmostEqual(resultado.baby_kg_total, 1.0)
        self.assertAlmostEqual(resultado.plastico_kg_total, 0.3)
        self.assertAlmostEqual(resultado.tubo_kg_total, 0.2)
