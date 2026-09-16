"""A amarração da cadeia: programado → cortado → enviado → apontado →
retornado → faturado.

Cada etapa só pode entregar o que a anterior entregou de fato. A regra de
negócio é a do chão de fábrica: se a facção devolve mais do que consta,
não foi mágica — foi corte que passou e não foi lançado. O conserto é
registrar o corte que faltou, e é exatamente isso que as travas forçam a
acontecer, em vez de deixar a OP seguir com a conta furada e o Balanço de
material mentir no fechamento.

Duas exceções, ambas deliberadas e testadas aqui pra não virarem descuido:
o corte pode passar do programado (cortar a mais é legítimo), e o link do
prestador aceita apontamento acima do enviado (a facção não tem como
lançar corte nem esperar — o excesso vira pendência na ficha).
"""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.aproveitamento import atualizar_status_programacao
from corte.models import ProgramacaoCorte, RegistroCorte

from .forms import (
    EnvioProducaoForm, FaturamentoParcialForm, RegistroProducaoForm, RetornoProducaoForm,
)
from .models import EnvioProducao, Prestador, RegistroProducao, RetornoProducao

TELA = override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)


class _Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.op = ProgramacaoCorte.objects.create(
            pedido="AMARRA-1", cliente="CAMESA", produto="JOGO DE CAMA",
            categoria="Jogo de cama", saldo_carteira_snap=100, qnt_programada=100,
            semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
            unidade_corte=UnidadeCorte.CORTINA, destino_costura="MEGA BARIRI",
            criado_por=self.user, origem=ProgramacaoCorte.Origem.SISTEMA)

    # ----- atalhos de lançamento -----
    def cortar(self, quantidade):
        registro = RegistroCorte.objects.create(
            programacao=self.op, unidade=self.op.unidade_corte, data=date(2026, 9, 1),
            quantidade_pecas=quantidade, criado_por=self.user)
        # A view de lançamento recalcula o status logo depois de gravar; sem
        # isso a OP fica PENDENTE com corte lançado e a etapa "Corte" nunca
        # fecha — o cenário deixaria de ser o que acontece de verdade.
        atualizar_status_programacao(self.op)
        self.op.refresh_from_db()
        return registro

    def enviar(self, quantidade, numero="4471"):
        return EnvioProducao.objects.create(
            programacao=self.op, data=date(2026, 9, 2), destino="MEGA BARIRI",
            quantidade_pecas=quantidade, criado_por=self.user, tipo="OSE", numero=numero)

    def apontar(self, quantidade):
        return RegistroProducao.objects.create(
            programacao=self.op, data=date(2026, 9, 3), quantidade_pecas=quantidade,
            destino="MEGA BARIRI", criado_por=self.user)

    def retornar(self, quantidade):
        return RetornoProducao.objects.create(
            programacao=self.op, data=date(2026, 9, 4), quantidade_pecas=quantidade,
            destino="MEGA BARIRI", criado_por=self.user)

    # ----- atalhos de formulário (é neles que a trava mora) -----
    def form_envio(self, quantidade, numero="9999"):
        return EnvioProducaoForm(
            {"data": "2026-09-02", "tipo": "OSE", "numero": numero,
             "destino": "MEGA BARIRI", "quantidade_pecas": str(quantidade), "observacao": ""},
            programacao=self.op)

    def form_producao(self, quantidade, segunda=0):
        return RegistroProducaoForm(
            {"data": "2026-09-03", "quantidade_pecas": str(quantidade),
             "qualidade_segunda_pecas": str(segunda), "retalho_kg": "", "observacao": ""},
            programacao=self.op)

    def form_retorno(self, quantidade):
        return RetornoProducaoForm(
            {"data": "2026-09-04", "quantidade_pecas": str(quantidade),
             "retalho_kg": "", "observacao": ""},
            programacao=self.op)

    def erros(self, form) -> str:
        form.is_valid()
        return " ".join(m for lista in form.errors.values() for m in lista)


class CorteTests(_Base):
    """Primeiro elo. Não tem etapa anterior pra conferir contra — o
    programado é uma intenção, não um fato."""

    def test_cortar_acima_do_programado_e_permitido(self):
        """Cortar a mais é rotina (sobra de rolo, acerto). Travar aqui
        impediria de registrar o que de fato aconteceu na mesa."""
        registro = self.cortar(130)
        self.assertEqual(registro.quantidade_pecas, 130)


class EnvioTests(_Base):
    """Segundo elo: só sai pra facção o que saiu da mesa de corte."""

    def test_sem_corte_nenhum_envio_e_recusado(self):
        self.assertIn("Só há 0 pçs cortadas", self.erros(self.form_envio(10)))

    def test_envio_acima_do_cortado_e_recusado(self):
        self.cortar(100)
        self.assertIn("Só há 100 pçs cortadas", self.erros(self.form_envio(120)))

    def test_envio_ate_o_cortado_passa(self):
        self.cortar(100)
        self.assertTrue(self.form_envio(100).is_valid())

    def test_soma_dos_envios_e_o_que_conta_nao_cada_um_isolado(self):
        """Dois envios de 60 numa OP de 100 cortadas: o segundo é recusado
        mesmo sendo menor que o total cortado."""
        self.cortar(100)
        self.enviar(60)
        self.assertIn("Só há 40 pçs cortadas", self.erros(self.form_envio(60)))

    def test_lancar_o_corte_que_faltou_destrava_o_envio(self):
        """O conserto que o chão de fábrica usa: apareceu peça a mais,
        registra o corte que passou e a movimentação volta a fechar."""
        self.cortar(100)
        self.enviar(100)
        self.assertFalse(self.form_envio(30).is_valid())

        self.cortar(30)  # o corte que tinha passado batido
        self.assertTrue(self.form_envio(30).is_valid())


class ProducaoTests(_Base):
    """Terceiro elo: a facção só produz o que recebeu."""

    def test_apontar_sem_envio_e_recusado(self):
        self.cortar(100)
        self.assertIn("registre a OS", self.erros(self.form_producao(10)))

    def test_apontar_acima_do_enviado_e_recusado(self):
        self.cortar(100)
        self.enviar(100)
        self.assertIn("Só há 100 pçs por apontar", self.erros(self.form_producao(120)))

    def test_segunda_qualidade_entra_na_conta_do_enviado(self):
        """2ª qualidade abate o saldo igual à peça boa — 90 + 20 passa das
        100 enviadas mesmo com só 90 de 1ª."""
        self.cortar(100)
        self.enviar(100)
        self.assertFalse(self.form_producao(90, segunda=20).is_valid())
        self.assertTrue(self.form_producao(80, segunda=20).is_valid())

    def test_apontar_ate_o_enviado_passa(self):
        self.cortar(100)
        self.enviar(100)
        self.assertTrue(self.form_producao(100).is_valid())


class RetornoTests(_Base):
    """Quarto elo: só volta o que foi apontado como produzido."""

    def test_retornar_acima_do_apontado_e_recusado(self):
        self.cortar(100)
        self.enviar(100)
        self.apontar(80)
        self.assertIn("Só há 80 pçs por retornar", self.erros(self.form_retorno(100)))

    def test_retornar_ate_o_apontado_passa(self):
        self.cortar(100)
        self.enviar(100)
        self.apontar(80)
        self.assertTrue(self.form_retorno(80).is_valid())

    def test_soma_dos_retornos_e_o_que_conta(self):
        self.cortar(100)
        self.enviar(100)
        self.apontar(100)
        self.retornar(70)
        self.assertIn("Só há 30 pçs por retornar", self.erros(self.form_retorno(40)))


class FaturamentoTests(_Base):
    """Último elo, e o único que avisa em vez de travar: faturar é o
    financeiro falando, não a física da produção. A tela dá visibilidade,
    não manda no ERP."""

    def test_faturar_acima_do_programado_avisa_mas_nao_trava(self):
        form = FaturamentoParcialForm({"quantidade_faturada": "150"}, programacao=self.op)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(getattr(form, "add_warning", None))


@TELA
class PrestadorEhAExcecaoTests(_Base):
    """O link do prestador aceita o que o lançamento interno recusa — de
    propósito. A facção não tem como lançar o corte que faltou nem esperar
    alguém lançar; travar ali só faria o apontamento não acontecer. Então o
    excesso entra e vira pendência na ficha, pra Zanattex regularizar."""

    def setUp(self):
        super().setUp()
        self.prestador = Prestador.objects.create(nome="MEGA BARIRI")
        self.cortar(100)
        self.enviar(100)
        self.apontar(100)

    def _apontar_pelo_link(self, quantidade):
        return self.client.post(
            reverse("controle_op:prestador_op", args=[self.prestador.token, self.op.id]),
            {"data": "2026-09-05", "quantidade_pecas": str(quantidade),
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": "",
             "criado_por_nome": "Fulano da facção"})

    def test_prestador_consegue_apontar_acima_do_enviado(self):
        self._apontar_pelo_link(20)
        total = sum(r.quantidade_pecas for r in self.op.registros_producao.all())
        self.assertEqual(total, 120)

    def test_excesso_do_prestador_vira_pendencia_na_ficha(self):
        self._apontar_pelo_link(20)
        self.client.force_login(self.user)
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.op.id]))
        self.assertEqual(resp.context["apontado_acima_do_enviado"], 20)
        self.assertIn("apontadas a mais do que foi enviado", resp.content.decode())

    def test_sem_excesso_nao_inventa_pendencia(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.op.id]))
        self.assertEqual(resp.context["apontado_acima_do_enviado"], 0)
        self.assertNotIn("apontadas a mais do que foi enviado", resp.content.decode())

    def test_regularizar_o_corte_e_o_envio_limpa_a_pendencia(self):
        """O caminho de saída: lança o corte que passou e a OS dele, e a
        conta volta a fechar sem ninguém apagar o apontamento da facção."""
        self._apontar_pelo_link(20)
        self.cortar(20)
        self.enviar(20, numero="4472")

        self.client.force_login(self.user)
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.op.id]))
        self.assertEqual(resp.context["apontado_acima_do_enviado"], 0)


@TELA
class CadeiaCompletaTests(_Base):
    """A OP inteira, de ponta a ponta, sem nenhum elo furado — é o caminho
    que precisa continuar funcionando depois de todas as travas."""

    def test_op_fecha_com_a_conta_batendo_em_todos_os_elos(self):
        self.client.force_login(self.user)

        def post(rota, dados):
            resp = self.client.post(reverse(f"controle_op:{rota}", args=[self.op.id]), dados)
            mensagens = [str(m) for m in resp.wsgi_request._messages]
            self.assertFalse(
                any("Confira" in m or "Só há" in m for m in mensagens),
                f"{rota} recusou o caminho feliz: {mensagens}")

        self.cortar(100)
        post("registrar_envio", {
            "data": "2026-09-02", "tipo": "OSE", "numero": "7001",
            "destino": "MEGA BARIRI", "quantidade_pecas": "100", "observacao": ""})
        post("registrar_producao", {
            "data": "2026-09-03", "quantidade_pecas": "100",
            "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""})
        post("registrar_retorno", {
            "data": "2026-09-04", "quantidade_pecas": "100",
            "retalho_kg": "", "observacao": ""})

        resp = self.client.get(reverse("controle_op:detalhe", args=[self.op.id]))
        estados = {e["nome"]: e["ok"] for e in resp.context["etapas"]}
        for etapa in ("Corte", "Envio", "Produção", "Retorno"):
            self.assertTrue(estados[etapa], f"{etapa} não fechou: {estados}")
        self.assertEqual(resp.context["apontado_acima_do_enviado"], 0)
