"""Fase 4 — Balanço de material. Testa `calcular_balanco` isolado da tela,
com dados controlados, porque é a fórmula mais arriscada do mini-ERP da OP
(qualquer componente None por engano vira INCOMPLETO errado; qualquer soma
errada vira DIVERGENTE ou FECHADO errado)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from contas.models import UnidadeCorte
from corte.models import ProgramacaoCorte, RegistroCorte

from corte.aproveitamento import calcular_aproveitamento

from . import relatorio_pdf
from .balanco import Base, StatusBalanco, calcular_balanco
from .models import RegistroProducao, RetornoProducao
from .producao import calcular_producao, producao_por_op


def _programacao(user, **campos) -> ProgramacaoCorte:
    dados = dict(
        pedido="12345", cliente="CAMESA", produto="MANTA CASAL",
        categoria="Manta", saldo_carteira_snap=100, qnt_programada=100,
        semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
        unidade_corte=UnidadeCorte.IACANGA_MANTA, destino_costura="MEGA BARIRI",
        criado_por=user, origem=ProgramacaoCorte.Origem.SISTEMA,
    )
    dados.update(campos)
    return ProgramacaoCorte.objects.create(**dados)


class BalancoMantaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def _op_completa(self, **campos_op):
        """OP fechando de ponta a ponta: 100 cortadas, 90 de 1ª + 10 de 2ª
        produzidas e retornadas por inteiro — nada em processo."""
        programacao = _programacao(self.user, **campos_op)
        RegistroCorte.objects.create(
            programacao=programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("55.00"),
            retalho_kg=Decimal("2.00"), gramatura=Decimal("0.5000"),
            baby_kg=Decimal("1.00"), plastico_kg=Decimal("0.30"), tubo_kg=Decimal("0.20"),
            criado_por=self.user,
        )
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2),
            quantidade_pecas=90, qualidade_segunda_pecas=10, retalho_kg=Decimal("1.00"),
            criado_por=self.user,
        )
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=100,
            criado_por=self.user,
        )
        return programacao

    def test_fecha_sem_requisitado_base_e_o_cortado(self):
        """95×0.5 + 5×0.5 + 0 + 2 + 1 + 0,5 + 1 = 54,5 — contra 55 pesado no
        Corte, 0,9% de divergência, dentro da tolerância."""
        programacao = self._op_completa()
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.base, Base.CORTADO)
        self.assertAlmostEqual(balanco.base_kg, 55.0)
        self.assertAlmostEqual(balanco.explicado_kg, 54.5)
        self.assertEqual(balanco.status, StatusBalanco.FECHADO)

    def test_fecha_com_requisitado_prevalece_sobre_cortado(self):
        programacao = self._op_completa(kg_requisitado=Decimal("54.50"))
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.base, Base.REQUISITADO)
        self.assertAlmostEqual(balanco.base_kg, 54.5)
        self.assertEqual(balanco.status, StatusBalanco.FECHADO)

    def test_reciclavel_entra_no_explicado(self):
        """0,5 kg de plástico+tubo — tirar isso da conta desfecharia a OP."""
        programacao = self._op_completa()
        balanco = calcular_balanco(programacao)
        self.assertAlmostEqual(balanco.reciclavel_kg, 0.5)
        self.assertAlmostEqual(balanco.explicado_kg, 54.5)

    def test_baby_ausente_no_iacanga_fica_incompleto(self):
        """Iacanga não tem planilha de baby (só o Arealva tem) — sem
        RegistroCorte.baby_kg preenchido, o componente é None de verdade,
        não 0."""
        programacao = _programacao(self.user)
        RegistroCorte.objects.create(
            programacao=programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("55.00"),
            retalho_kg=Decimal("2.00"), gramatura=Decimal("0.5000"),
            plastico_kg=Decimal("0.30"), tubo_kg=Decimal("0.20"),
            criado_por=self.user,
        )
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2),
            quantidade_pecas=90, qualidade_segunda_pecas=10, retalho_kg=Decimal("1.00"),
            criado_por=self.user,
        )
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=100,
            criado_por=self.user,
        )
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.INCOMPLETO)
        self.assertIn("baby", balanco.faltando)
        self.assertIsNone(balanco.baby_kg)

    def test_gramatura_ausente_fica_incompleto(self):
        programacao = _programacao(self.user)
        RegistroCorte.objects.create(
            programacao=programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("55.00"),
            retalho_kg=Decimal("2.00"), baby_kg=Decimal("1.00"),
            criado_por=self.user,
        )
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.INCOMPLETO)
        self.assertTrue(any("processo" in item for item in balanco.faltando))

    def test_divergencia_acima_da_tolerancia_fica_divergente(self):
        """Pesou 80 no Corte, mas só 54,5 kg estão explicados — 31% de
        buraco, bem acima dos 2% de tolerância. divergencia_kg = base −
        explicado, então positivo aqui significa "faltou explicar"."""
        programacao = self._op_completa()
        RegistroCorte.objects.filter(programacao=programacao).update(kg_cortado=Decimal("80.00"))
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.DIVERGENTE)
        self.assertGreater(balanco.divergencia_kg, 0)

    def test_divergencia_negativa_tambem_fica_divergente(self):
        """Requisitado (40) menor que o explicado (54,5) — "sobrou"
        material explicado sem base pra ele, também é divergência (aqui
        divergencia_kg = base − explicado fica negativo)."""
        programacao = self._op_completa(kg_requisitado=Decimal("40.00"))
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.DIVERGENTE)
        self.assertLess(balanco.divergencia_kg, 0)

    def test_material_em_processo_nao_e_pendencia_falsa(self):
        """OP recém cortada, nada enviado/produzido/retornado ainda — nada
        "faltando" (peça boa/2ª são 0 de verdade, não dado ausente), mas
        também não fecha: ainda está em processo. Retalho/baby/reciclável
        são medidos NA HORA do corte (mesmo registro), então zerados
        explicitamente aqui — o que falta é só o que ainda não aconteceu
        (envio/produção/retorno), não dado de corte não preenchido."""
        programacao = _programacao(self.user)
        RegistroCorte.objects.create(
            programacao=programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("55.00"),
            gramatura=Decimal("0.5000"), retalho_kg=Decimal("0.00"),
            baby_kg=Decimal("0.00"), plastico_kg=Decimal("0.00"), tubo_kg=Decimal("0.00"),
            criado_por=self.user,
        )
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.peca_boa_kg, 0)
        self.assertEqual(balanco.segunda_kg, 0)
        self.assertAlmostEqual(balanco.em_processo_kg, 50.0)  # 100 peças × 0,5
        self.assertEqual(balanco.status, StatusBalanco.EM_PROCESSO)

    def test_aproveitamento_1a_qualidade_existe_mesmo_incompleto(self):
        """Indicador de eficiência não trava no critério de fechamento."""
        programacao = self._op_completa()
        RegistroCorte.objects.filter(programacao=programacao).update(baby_kg=None)
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.INCOMPLETO)
        self.assertIsNotNone(balanco.aproveitamento_1a_qualidade_pct)


class BalancoLencolTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def _registro_lencol(self, programacao, *, kg_por_metro="0.5"):
        extra = {"metros_por_peca": "1.0"}
        if kg_por_metro is not None:
            extra["kg_por_metro"] = kg_por_metro
        return RegistroCorte.objects.create(
            programacao=programacao, unidade=UnidadeCorte.LENCOL,
            data=date(2026, 9, 1), quantidade_pecas=100, metros_cortado=Decimal("106.00"),
            retalho_kg=Decimal("2.00"), extra=extra, criado_por=self.user,
        )

    def test_fecha_com_kg_por_metro(self):
        """106 m × 0,5 kg/m = 53 kg de base; 90×0,5 + 10×0,5 + 0 + 2 + 1 =
        53 kg explicados — bate exato."""
        programacao = _programacao(self.user, unidade_corte=UnidadeCorte.LENCOL)
        self._registro_lencol(programacao)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2),
            quantidade_pecas=90, qualidade_segunda_pecas=10, retalho_kg=Decimal("1.00"),
            criado_por=self.user,
        )
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=100,
            criado_por=self.user,
        )
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.base, Base.CORTADO)
        self.assertAlmostEqual(balanco.base_kg, 53.0)
        self.assertAlmostEqual(balanco.explicado_kg, 53.0, places=1)
        self.assertEqual(balanco.status, StatusBalanco.FECHADO)
        # Baby/reciclável não existem pra Lençol — não é "faltando".
        self.assertIsNone(balanco.baby_kg)
        self.assertNotIn("baby", balanco.faltando)

    def test_sem_kg_por_metro_fica_incompleto(self):
        programacao = _programacao(self.user, unidade_corte=UnidadeCorte.LENCOL)
        self._registro_lencol(programacao, kg_por_metro=None)
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.INCOMPLETO)
        self.assertIsNone(balanco.base_kg)


class BalancoSemGrandezaTests(TestCase):
    """Cortina e Itaju não têm nenhuma grandeza de material na fonte real
    — o balanço não pode fingir uma pendência que não existe."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def test_cortina_nao_aplicavel(self):
        programacao = _programacao(self.user, unidade_corte=UnidadeCorte.CORTINA)
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.NAO_APLICAVEL)
        self.assertEqual(balanco.faltando, [])

    def test_itaju_nao_aplicavel(self):
        programacao = _programacao(self.user, unidade_corte=UnidadeCorte.ITAJU)
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.NAO_APLICAVEL)

    def test_unidade_em_branco_nao_aplicavel(self):
        """OP antiga (backfill), sem unidade_corte definida."""
        programacao = _programacao(self.user, unidade_corte="")
        balanco = calcular_balanco(programacao)
        self.assertEqual(balanco.status, StatusBalanco.NAO_APLICAVEL)


class BalancoNoPdfTests(TestCase):
    """A seção de Balanço no PDF de fechamento tem caminhos de renderização
    diferentes pra FECHADO/DIVERGENTE (tabela de componentes) e INCOMPLETO
    (só a lista do que falta) — os dois precisam gerar PDF sem quebrar."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def _pdf(self, programacao):
        aproveitamento = calcular_aproveitamento(programacao)
        acumulada = producao_por_op(programacao)
        producao = calcular_producao(programacao, produzido_total=acumulada.produzido_total)
        balanco = calcular_balanco(
            programacao, aproveitamento=aproveitamento, producao=producao, acumulada=acumulada)
        return relatorio_pdf.gerar_pdf_fechamento(
            programacao=programacao, aproveitamento=aproveitamento,
            registros=list(programacao.registros.all()), producao=producao,
            acumulada=acumulada, balanco=balanco,
            envios=[], retornos=list(programacao.retornos_producao.all()),
            producoes=list(programacao.registros_producao.all()),
        )

    def test_pdf_com_balanco_fechado(self):
        programacao = _programacao(self.user)
        RegistroCorte.objects.create(
            programacao=programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("55.00"),
            retalho_kg=Decimal("2.00"), gramatura=Decimal("0.5000"), baby_kg=Decimal("1.00"),
            plastico_kg=Decimal("0.30"), tubo_kg=Decimal("0.20"), criado_por=self.user,
        )
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2),
            quantidade_pecas=90, qualidade_segunda_pecas=10, retalho_kg=Decimal("1.00"),
            criado_por=self.user,
        )
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=100,
            criado_por=self.user,
        )
        pdf = self._pdf(programacao)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_pdf_com_balanco_incompleto(self):
        programacao = _programacao(self.user)
        RegistroCorte.objects.create(
            programacao=programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, criado_por=self.user,
        )
        pdf = self._pdf(programacao)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_pdf_com_balanco_nao_aplicavel_nao_adiciona_secao(self):
        """Cortina não deve nem tentar montar a seção — só confirma que não
        quebra (a ausência da seção não é fácil de checar no PDF binário)."""
        programacao = _programacao(self.user, unidade_corte=UnidadeCorte.CORTINA)
        pdf = self._pdf(programacao)
        self.assertTrue(pdf.startswith(b"%PDF"))
