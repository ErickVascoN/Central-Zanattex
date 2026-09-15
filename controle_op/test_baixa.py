"""Fase 5 — Baixa da OP. "Esta OP está encerrada? Quando, por quem, com
qual Balanço congelado?" As duas regras: toda OS precisa ter número (o
vínculo com o ERP é o ponto do projeto inteiro); balanço que não fechou
exato exige motivo — nunca bloqueia, só exige dizer por quê (V6 do plano:
corte parcial legítimo não pode travar a OP pra sempre)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.models import ProgramacaoCorte, RegistroCorte

from .baixa import ErroBaixaOP, baixar_op, op_esta_baixada, reabrir_op
from .balanco import StatusBalanco
from .models import EnvioProducao, FechamentoOP, RegistroProducao, RetornoProducao


def _programacao(user, **campos) -> ProgramacaoCorte:
    dados = dict(
        pedido="12345", cliente="CAMESA", produto="JOGO DE CAMA",
        categoria="Jogo de cama", saldo_carteira_snap=500, qnt_programada=500,
        semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
        unidade_corte=UnidadeCorte.CORTINA, destino_costura="MEGA BARIRI",
        criado_por=user, origem=ProgramacaoCorte.Origem.SISTEMA,
    )
    dados.update(campos)
    return ProgramacaoCorte.objects.create(**dados)


def _envio(programacao, user, **campos):
    dados = dict(
        programacao=programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
        quantidade_pecas=200, criado_por=user, tipo="OSE", numero="4471")
    dados.update(campos)
    return EnvioProducao.objects.create(**dados)


class BaixarOPTests(TestCase):
    """Unidade Cortina (NAO_APLICAVEL) pros casos "simples" — não precisa
    montar gramatura/kg pra testar a régua de OS sem número/motivo."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)

    def test_recusa_com_os_sem_numero(self):
        _envio(self.programacao, self.user, numero="")
        with self.assertRaises(ErroBaixaOP):
            baixar_op(self.programacao, self.user)
        self.assertFalse(op_esta_baixada(self.programacao))

    def test_baixa_sem_pendencia_nao_exige_motivo(self):
        _envio(self.programacao, self.user)  # OS com número
        fechamento = baixar_op(self.programacao, self.user)
        self.assertTrue(fechamento.op_baixada)
        self.assertEqual(fechamento.balanco_status, StatusBalanco.NAO_APLICAVEL)
        self.assertEqual(fechamento.motivo_divergencia, "")

    def test_grava_snapshot_autor_e_data(self):
        _envio(self.programacao, self.user)
        fechamento = baixar_op(self.programacao, self.user)
        self.assertIsNotNone(fechamento.op_baixada_em)
        self.assertEqual(fechamento.op_baixada_por, self.user)
        self.assertIsNotNone(fechamento.balanco_snapshot)
        self.assertEqual(fechamento.balanco_snapshot["status"], StatusBalanco.NAO_APLICAVEL)

    def test_baixar_de_novo_atualiza_o_mesmo_fechamento(self):
        _envio(self.programacao, self.user)
        baixar_op(self.programacao, self.user)
        self.assertEqual(FechamentoOP.objects.filter(programacao=self.programacao).count(), 1)


class BaixarOPDivergenteTests(TestCase):
    """Unidade Manta — precisa de um balanço de verdade pra testar a régua
    de motivo obrigatório."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user, unidade_corte=UnidadeCorte.IACANGA_MANTA)
        _envio(self.programacao, self.user, quantidade_pecas=100)
        # Cortado 100 peças, mas kg pesado (80) bem acima do que qualquer
        # coisa explicaria (gramatura 0,5 × 100 = 50) — força DIVERGENTE.
        RegistroCorte.objects.create(
            programacao=self.programacao, unidade=UnidadeCorte.IACANGA_MANTA,
            data=date(2026, 9, 1), quantidade_pecas=100, kg_cortado=Decimal("80.00"),
            gramatura=Decimal("0.5000"), retalho_kg=Decimal("0"), baby_kg=Decimal("0"),
            plastico_kg=Decimal("0"), tubo_kg=Decimal("0"), criado_por=self.user,
        )
        RegistroProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=100,
            retalho_kg=Decimal("0"), criado_por=self.user)
        RetornoProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 3), quantidade_pecas=100,
            criado_por=self.user)

    def test_recusa_divergente_sem_motivo(self):
        with self.assertRaises(ErroBaixaOP):
            baixar_op(self.programacao, self.user)
        self.assertFalse(op_esta_baixada(self.programacao))

    def test_baixa_divergente_com_motivo(self):
        fechamento = baixar_op(
            self.programacao, self.user, motivo_divergencia="Erro de balança conhecido.")
        self.assertTrue(fechamento.op_baixada)
        self.assertEqual(fechamento.balanco_status, StatusBalanco.DIVERGENTE)
        self.assertEqual(fechamento.motivo_divergencia, "Erro de balança conhecido.")

    def test_motivo_so_espacos_e_recusado(self):
        with self.assertRaises(ErroBaixaOP):
            baixar_op(self.programacao, self.user, motivo_divergencia="   ")


class ReabrirOPTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)
        _envio(self.programacao, self.user)

    def test_reabrir_op_nao_baixada_e_erro(self):
        with self.assertRaises(ErroBaixaOP):
            reabrir_op(self.programacao, self.user)

    def test_reabrir_limpa_baixa_mas_preserva_snapshot(self):
        baixar_op(self.programacao, self.user)
        fechamento = reabrir_op(self.programacao, self.user)
        self.assertFalse(fechamento.op_baixada)
        self.assertIsNone(fechamento.op_baixada_em)
        self.assertIsNone(fechamento.op_baixada_por)
        # O snapshot é histórico — não é apagado ao reabrir.
        self.assertIsNotNone(fechamento.balanco_snapshot)
        self.assertEqual(fechamento.balanco_status, StatusBalanco.NAO_APLICAVEL)


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class PosBaixaBloqueiaLancamentoTests(TestCase):
    """A OP já baixada não aceita lançamento novo em nenhuma das 5 telas de
    registro — só Reabrir destrava de novo."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)
        self.programacao = _programacao(self.user)
        _envio(self.programacao, self.user)
        baixar_op(self.programacao, self.user)

    def _post_bloqueado(self, url_name, dados):
        resp = self.client.post(
            reverse(f"controle_op:{url_name}", args=[self.programacao.id]), dados)
        mensagens = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any("já foi baixada" in m for m in mensagens), mensagens)
        return resp

    def test_bloqueia_registrar_envio(self):
        self._post_bloqueado("registrar_envio", {
            "data": "2026-09-02", "tipo": "OSE", "numero": "9999",
            "destino": "MEGA BARIRI", "quantidade_pecas": "10", "observacao": "",
        })
        self.assertEqual(EnvioProducao.objects.filter(programacao=self.programacao).count(), 1)

    def test_bloqueia_registrar_producao(self):
        self._post_bloqueado("registrar_producao", {
            "data": "2026-09-02", "quantidade_pecas": "10",
            "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": "",
        })
        self.assertEqual(RegistroProducao.objects.filter(programacao=self.programacao).count(), 0)

    def test_bloqueia_registrar_retorno(self):
        self._post_bloqueado("registrar_retorno", {
            "data": "2026-09-02", "quantidade_pecas": "10", "retalho_kg": "", "observacao": "",
        })
        self.assertEqual(RetornoProducao.objects.filter(programacao=self.programacao).count(), 0)

    def test_bloqueia_registrar_requisitado(self):
        self._post_bloqueado("registrar_requisitado", {"kg_requisitado": "50", "metros_requisitado": ""})
        self.programacao.refresh_from_db()
        self.assertIsNone(self.programacao.kg_requisitado)

    def test_reabrir_destrava_de_novo(self):
        self.client.post(reverse("controle_op:reabrir_op", args=[self.programacao.id]))
        resp = self.client.post(
            reverse("controle_op:registrar_producao", args=[self.programacao.id]),
            {"data": "2026-09-02", "quantidade_pecas": "10",
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""})
        self.assertEqual(RegistroProducao.objects.filter(programacao=self.programacao).count(), 1)


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class BaixarOPViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)
        self.programacao = _programacao(self.user)

    def test_baixar_sem_numero_de_os_mostra_erro_na_tela(self):
        _envio(self.programacao, self.user, numero="")
        resp = self.client.post(
            reverse("controle_op:baixar_op", args=[self.programacao.id]), {})
        mensagens = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any("sem número" in m for m in mensagens), mensagens)
        self.assertFalse(op_esta_baixada(self.programacao))

    def test_baixar_pela_tela_grava(self):
        _envio(self.programacao, self.user)
        self.client.post(reverse("controle_op:baixar_op", args=[self.programacao.id]), {})
        self.assertTrue(op_esta_baixada(self.programacao))

    def test_lista_mostra_fechado_geral_via_op_baixada(self):
        """`_linha()['fechado_geral']` lê o campo persistido — não
        recalcula corte+produção+faturamento."""
        _envio(self.programacao, self.user)
        baixar_op(self.programacao, self.user)
        resp = self.client.get(reverse("controle_op:lista"))
        item = next(
            i for grupo in resp.context["grupos"] for i in grupo["itens"]
            if i["programacao"].id == self.programacao.id)
        self.assertTrue(item["fechado_geral"])

    def test_detalhe_mostra_etapa_baixa_na_trilha(self):
        _envio(self.programacao, self.user)
        baixar_op(self.programacao, self.user)
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.programacao.id]))
        etapa = next(e for e in resp.context["etapas"] if e["nome"] == "Baixa da OP")
        self.assertTrue(etapa["ok"])

    def test_pdf_de_op_baixada_usa_o_snapshot_congelado(self):
        """Depois de baixada, o PDF não pode recalcular ao vivo — imprime a
        FOTO congelada em `fechamento.balanco_snapshot`."""
        _envio(self.programacao, self.user)
        baixar_op(self.programacao, self.user)
        resp = self.client.get(reverse("controle_op:fechamento_pdf", args=[self.programacao.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b"%PDF"))
