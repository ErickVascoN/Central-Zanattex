"""Painel de disparo do link de apontamento (botão wa.me por prestador) —
só entra quem é Controladoria/PCP e tem pelo menos uma OP em aberto; o
envio continua manual, isto só poupa caçar o link no admin."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.models import ProgramacaoCorte

from .models import EnvioProducao, Prestador


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
        quantidade_pecas=quantidade, criado_por=user, tipo="OSE",
        numero=f"OS-{programacao.pedido}-{destino}")


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class DisparoPrestadoresViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)
        self.mega = Prestador.objects.create(nome="MEGA BARIRI", telefone="5514999998888")
        self.zaro = Prestador.objects.create(nome="ZARO (LUIS)")  # sem telefone

    def test_so_lista_prestador_com_op_em_aberto(self):
        p1 = _programacao(self.user, pedido="111")
        _envio(p1, self.user, "MEGA BARIRI", 100)
        # ZARO nunca recebeu envio — não deve aparecer.
        resp = self.client.get(reverse("controle_op:disparo_prestadores"))
        nomes = [linha["prestador"].nome for linha in resp.context["linhas"]]
        self.assertIn("MEGA BARIRI", nomes)
        self.assertNotIn("ZARO (LUIS)", nomes)

    def test_whatsapp_url_leva_telefone_e_link_do_prestador(self):
        p1 = _programacao(self.user, pedido="111")
        _envio(p1, self.user, "MEGA BARIRI", 100)
        resp = self.client.get(reverse("controle_op:disparo_prestadores"))
        linha = next(l for l in resp.context["linhas"] if l["prestador"] == self.mega)
        self.assertTrue(linha["whatsapp_url"].startswith("https://wa.me/5514999998888?text="))
        self.assertIn(self.mega.token, linha["link"])

    @override_settings(SITE_URL="https://central-zanattex.fly.dev")
    def test_link_usa_site_url_fixo_nao_o_host_da_requisicao(self):
        """Regressão: o link já saiu com host 127.0.0.1 (de quem disparou
        rodando local) pro celular de um prestador de verdade — precisa vir
        sempre do SITE_URL fixo, nunca de request.build_absolute_uri()."""
        p1 = _programacao(self.user, pedido="111")
        _envio(p1, self.user, "MEGA BARIRI", 100)
        # O client de teste do Django navega em "testserver" por padrão —
        # se o link seguisse o host da requisição, apareceria aqui.
        resp = self.client.get(reverse("controle_op:disparo_prestadores"))
        linha = next(l for l in resp.context["linhas"] if l["prestador"] == self.mega)
        self.assertTrue(linha["link"].startswith("https://central-zanattex.fly.dev/"))
        self.assertNotIn("testserver", linha["link"])

    def test_sem_telefone_nao_gera_whatsapp_url(self):
        p1 = _programacao(self.user, pedido="222")
        _envio(p1, self.user, "ZARO (LUIS)", 50)
        resp = self.client.get(reverse("controle_op:disparo_prestadores"))
        linha = next(l for l in resp.context["linhas"] if l["prestador"] == self.zaro)
        self.assertIsNone(linha["whatsapp_url"])
        self.assertTrue(linha["link"])  # link puro continua disponível

    def test_prestador_inativo_nao_aparece(self):
        p1 = _programacao(self.user, pedido="111")
        _envio(p1, self.user, "MEGA BARIRI", 100)
        self.mega.ativo = False
        self.mega.save()
        resp = self.client.get(reverse("controle_op:disparo_prestadores"))
        nomes = [linha["prestador"].nome for linha in resp.context["linhas"]]
        self.assertNotIn("MEGA BARIRI", nomes)

    def test_op_ja_fechada_para_o_prestador_nao_conta(self):
        from .models import RegistroProducao, RetornoProducao
        p1 = _programacao(self.user, pedido="111")
        _envio(p1, self.user, "MEGA BARIRI", 80)
        RegistroProducao.objects.create(
            programacao=p1, data=date(2026, 9, 2), quantidade_pecas=80,
            destino="MEGA BARIRI", criado_por=self.user)
        RetornoProducao.objects.create(
            programacao=p1, data=date(2026, 9, 4), quantidade_pecas=80,
            destino="MEGA BARIRI", criado_por=self.user)
        resp = self.client.get(reverse("controle_op:disparo_prestadores"))
        nomes = [linha["prestador"].nome for linha in resp.context["linhas"]]
        self.assertNotIn("MEGA BARIRI", nomes)

    def test_exige_setor_controladoria(self):
        from contas.models import PerfilUsuario, Setor
        corte_user = get_user_model().objects.create_user("cortador", password="x")
        PerfilUsuario.objects.create(usuario=corte_user, setor=Setor.CORTE)
        client = self.client_class()
        client.force_login(corte_user)
        resp = client.get(reverse("controle_op:disparo_prestadores"))
        self.assertNotEqual(resp.status_code, 200)
