"""Saldo por prestador — pré-requisito da Fase 2b (link do prestador).
Dividir uma OP entre prestadores é comum (confirmado com o usuário), então
`RegistroProducao`/`RetornoProducao` precisam saber de qual prestador veio
cada apontamento/retorno pra separar o saldo, não só somar pra OP inteira."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.models import ProgramacaoCorte

from .forms import RegistroProducaoForm, RetornoProducaoForm
from .models import EnvioProducao, RegistroProducao, RetornoProducao
from .producao import NAO_INFORMADO, saldo_por_prestador


def _programacao(user, **campos) -> ProgramacaoCorte:
    dados = dict(
        pedido="12345", cliente="CAMESA", produto="JOGO DE CAMA",
        categoria="Jogo de cama", saldo_carteira_snap=500, qnt_programada=500,
        semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
        unidade_corte=UnidadeCorte.values[0], destino_costura="MEGA BARIRI",
        criado_por=user, origem=ProgramacaoCorte.Origem.SISTEMA,
    )
    dados.update(campos)
    return ProgramacaoCorte.objects.create(**dados)


def _envio(programacao, user, destino, quantidade):
    return EnvioProducao.objects.create(
        programacao=programacao, data=date(2026, 9, 1), destino=destino,
        quantidade_pecas=quantidade, criado_por=user, tipo="OSE", numero=f"OS-{destino}")


class DestinoOpcionalNoFormTests(TestCase):
    """Sem atrito quando só há um prestador; obrigatório escolher quando
    já existe mais de um."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def test_um_prestador_so_nao_pede_escolha(self):
        programacao = _programacao(self.user)
        _envio(programacao, self.user, "MEGA BARIRI", 200)
        form = RegistroProducaoForm(programacao=programacao)
        self.assertNotIn("destino", form.fields)

    def test_zero_envios_nao_pede_escolha(self):
        programacao = _programacao(self.user)
        form = RetornoProducaoForm(programacao=programacao)
        self.assertNotIn("destino", form.fields)

    def test_dois_prestadores_exige_escolha(self):
        programacao = _programacao(self.user)
        _envio(programacao, self.user, "MEGA BARIRI", 200)
        _envio(programacao, self.user, "ZARO (LUIS)", 100)
        form = RegistroProducaoForm(programacao=programacao)
        self.assertIn("destino", form.fields)
        self.assertTrue(form.fields["destino"].required)
        valores = [v for v, _ in form.fields["destino"].choices]
        self.assertIn("MEGA BARIRI", valores)
        self.assertIn("ZARO (LUIS)", valores)

    def test_apontamento_sem_prestador_falha_quando_ha_dois(self):
        programacao = _programacao(self.user)
        _envio(programacao, self.user, "MEGA BARIRI", 200)
        _envio(programacao, self.user, "ZARO (LUIS)", 100)
        form = RegistroProducaoForm(
            {"data": "2026-09-02", "quantidade_pecas": "100",
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""},
            programacao=programacao)
        self.assertFalse(form.is_valid())
        self.assertIn("destino", form.errors)

    def test_com_um_prestador_grava_sozinho_sem_pedir(self):
        programacao = _programacao(self.user)
        _envio(programacao, self.user, "MEGA BARIRI", 200)
        form = RegistroProducaoForm(
            {"data": "2026-09-02", "quantidade_pecas": "100",
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""},
            programacao=programacao)
        self.assertTrue(form.is_valid(), form.errors)
        registro = form.save(commit=False)
        registro.programacao = programacao
        registro.criado_por = self.user
        registro.save()
        self.assertEqual(registro.destino, "MEGA BARIRI")


class SaldoPorPrestadorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def test_um_prestador_bate_com_o_saldo_da_op_inteira(self):
        programacao = _programacao(self.user)
        _envio(programacao, self.user, "MEGA BARIRI", 200)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2), quantidade_pecas=180,
            qualidade_segunda_pecas=20, destino="MEGA BARIRI", criado_por=self.user)
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=150,
            destino="MEGA BARIRI", criado_por=self.user)

        saldo = saldo_por_prestador(programacao)
        self.assertEqual(len(saldo), 1)
        item = saldo[0]
        self.assertEqual(item.destino, "MEGA BARIRI")
        self.assertEqual(item.enviado_pecas, 200)
        self.assertEqual(item.produzido_pecas, 200)
        self.assertEqual(item.retornado_pecas, 150)
        self.assertEqual(item.saldo_a_retornar, 50)

    def test_dois_prestadores_saldo_separado(self):
        programacao = _programacao(self.user)
        _envio(programacao, self.user, "MEGA BARIRI", 200)
        _envio(programacao, self.user, "ZARO (LUIS)", 100)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2), quantidade_pecas=200,
            destino="MEGA BARIRI", criado_por=self.user)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2), quantidade_pecas=100,
            destino="ZARO (LUIS)", criado_por=self.user)
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=200,
            destino="MEGA BARIRI", criado_por=self.user)
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=40,
            destino="ZARO (LUIS)", criado_por=self.user)

        saldo = {item.destino: item for item in saldo_por_prestador(programacao)}
        self.assertEqual(len(saldo), 2)
        self.assertEqual(saldo["MEGA BARIRI"].saldo_a_retornar, 0)   # 200 produzido, 200 retornado
        self.assertEqual(saldo["ZARO (LUIS)"].saldo_a_retornar, 60)  # 100 produzido, 40 retornado

    def test_dado_sem_prestador_vira_pendencia_nao_informado(self):
        """Apontamento sem `destino` enquanto a OP já tem 2 prestadores —
        não pode ser silenciosamente ignorado nem atribuído a um dos dois
        sem confirmação."""
        programacao = _programacao(self.user)
        _envio(programacao, self.user, "MEGA BARIRI", 200)
        _envio(programacao, self.user, "ZARO (LUIS)", 100)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2), quantidade_pecas=50,
            destino="", criado_por=self.user)

        saldo = {item.destino: item for item in saldo_por_prestador(programacao)}
        self.assertIn(NAO_INFORMADO, saldo)
        self.assertEqual(saldo[NAO_INFORMADO].produzido_pecas, 50)

    def test_sem_nenhum_envio_lista_vazia(self):
        programacao = _programacao(self.user)
        self.assertEqual(saldo_por_prestador(programacao), [])


@override_settings(ROOT_URLCONF="controle_op.test_urls")
class RegistrarComPrestadorViewTests(TestCase):
    """Fluxo completo pela tela: dois envios pra prestadores diferentes,
    apontamento tem que exigir a escolha."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)
        self.programacao = _programacao(self.user)
        _envio(self.programacao, self.user, "MEGA BARIRI", 200)
        _envio(self.programacao, self.user, "ZARO (LUIS)", 100)

    def test_apontamento_sem_prestador_nao_grava_e_explica(self):
        resp = self.client.post(
            reverse("controle_op:registrar_producao", args=[self.programacao.id]),
            {"data": "2026-09-02", "quantidade_pecas": "100",
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""})
        self.assertEqual(RegistroProducao.objects.count(), 0)
        mensagens = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(mensagens, mensagens)

    def test_apontamento_com_prestador_grava(self):
        self.client.post(
            reverse("controle_op:registrar_producao", args=[self.programacao.id]),
            {"data": "2026-09-02", "destino": "ZARO (LUIS)", "quantidade_pecas": "100",
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""})
        registro = RegistroProducao.objects.get()
        self.assertEqual(registro.destino, "ZARO (LUIS)")
