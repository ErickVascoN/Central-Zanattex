"""Faturamento parcial — OPs grandes faturam em várias NFs, bem antes de a
OP terminar de ser produzida ou de ser baixada. `FechamentoOP.
quantidade_faturada` é só um total corrente, editável a qualquer momento,
independente da Baixa (não bloqueia, nem é bloqueado por ela)."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.models import ProgramacaoCorte

from .baixa import baixar_op
from .models import EnvioProducao, FechamentoOP


def _programacao(user, **campos) -> ProgramacaoCorte:
    dados = dict(
        pedido="12345", cliente="CAMESA", produto="JOGO DE CAMA",
        categoria="Jogo de cama", saldo_carteira_snap=500, qnt_programada=1000,
        semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
        unidade_corte=UnidadeCorte.CORTINA, destino_costura="MEGA BARIRI",
        criado_por=user, origem=ProgramacaoCorte.Origem.SISTEMA,
    )
    dados.update(campos)
    return ProgramacaoCorte.objects.create(**dados)


def _envio(programacao, user, **campos):
    dados = dict(
        programacao=programacao, data="2026-09-01", destino="MEGA BARIRI",
        quantidade_pecas=200, criado_por=user, tipo="OSE", numero="4471")
    dados.update(campos)
    return EnvioProducao.objects.create(**dados)


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class AtualizarFaturamentoParcialTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("financeiro", password="x")
        self.client.force_login(self.user)
        self.programacao = _programacao(self.user)

    def test_cria_fechamento_e_grava_quantidade(self):
        self.client.post(
            reverse("controle_op:atualizar_faturamento_parcial", args=[self.programacao.id]),
            {"quantidade_faturada": "300"})
        fechamento = FechamentoOP.objects.get(programacao=self.programacao)
        self.assertEqual(fechamento.quantidade_faturada, 300)

    def test_atualizar_de_novo_substitui_o_total_no_mesmo_registro(self):
        url = reverse("controle_op:atualizar_faturamento_parcial", args=[self.programacao.id])
        self.client.post(url, {"quantidade_faturada": "300"})
        self.client.post(url, {"quantidade_faturada": "700"})
        self.assertEqual(FechamentoOP.objects.filter(programacao=self.programacao).count(), 1)
        fechamento = FechamentoOP.objects.get(programacao=self.programacao)
        self.assertEqual(fechamento.quantidade_faturada, 700)

    def test_acima_do_programado_avisa_mas_nao_bloqueia(self):
        resp = self.client.post(
            reverse("controle_op:atualizar_faturamento_parcial", args=[self.programacao.id]),
            {"quantidade_faturada": "1500"}, follow=True)
        mensagens = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("mais do que" in m for m in mensagens), mensagens)
        fechamento = FechamentoOP.objects.get(programacao=self.programacao)
        self.assertEqual(fechamento.quantidade_faturada, 1500)

    def test_nao_e_bloqueado_por_op_ja_baixada(self):
        """Diferente das 5 telas de lançamento (Fase 5) — faturar em partes
        continua liberado mesmo com a OP já baixada (a última NF de uma OP
        grande costuma sair depois do encerramento)."""
        _envio(self.programacao, self.user)
        baixar_op(self.programacao, self.user)
        self.client.post(
            reverse("controle_op:atualizar_faturamento_parcial", args=[self.programacao.id]),
            {"quantidade_faturada": "1000"})
        fechamento = FechamentoOP.objects.get(programacao=self.programacao)
        self.assertEqual(fechamento.quantidade_faturada, 1000)

    def test_detalhe_mostra_percentual_faturado(self):
        self.client.post(
            reverse("controle_op:atualizar_faturamento_parcial", args=[self.programacao.id]),
            {"quantidade_faturada": "250"})
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.programacao.id]))
        self.assertEqual(resp.context["pct_faturado"], 0.25)

    def test_detalhe_sem_fechamento_ainda_nao_quebra(self):
        """Antes de qualquer NF sair não existe FechamentoOP — a tela não
        pode quebrar por isso."""
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.programacao.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["pct_faturado"], 0.0)
