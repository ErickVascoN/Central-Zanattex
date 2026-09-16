"""Fase 1 da Gestão de OP — o envio vira a OS do ERP (OSE/OSI + número), que
é o vínculo que o ERP não tem entre pedido, corte e industrialização."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.aproveitamento import calcular_aproveitamento
from corte.models import ProgramacaoCorte

from . import relatorio_pdf
from .forms import EnvioProducaoForm, RegistroProducaoForm, RetornoProducaoForm
from .models import EnvioProducao, RegistroProducao, RetornoProducao, tipo_os_sugerido
from .producao import StatusProducao, calcular_producao, producao_por_op


def _programacao(user, **campos) -> ProgramacaoCorte:
    dados = dict(
        pedido="12345", cliente="CAMESA", produto="JOGO DE CAMA",
        categoria="Jogo de cama", saldo_carteira_snap=500, qnt_programada=500,
        semana="2026-S36", local=ProgramacaoCorte.Local.ZANATTEX,
        unidade_corte=UnidadeCorte.values[0], destino_costura="MEGA BARIRI",
        prev_corte=date(2026, 8, 31), criado_por=user,
        origem=ProgramacaoCorte.Origem.SISTEMA,
    )
    dados.update(campos)
    return ProgramacaoCorte.objects.create(**dados)


class EnvioOSTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)

    def _envio(self, **campos) -> EnvioProducao:
        dados = dict(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=200, criado_por=self.user)
        dados.update(campos)
        return EnvioProducao.objects.create(**dados)

    def test_tipo_e_numero_ficam_gravados_na_os(self):
        envio = self._envio(tipo=EnvioProducao.Tipo.OSE, numero="4471")
        envio.refresh_from_db()
        self.assertEqual(envio.tipo, "OSE")
        self.assertEqual(envio.numero, "4471")
        self.assertEqual(envio.os_label, "OSE 4471")
        self.assertFalse(envio.sem_numero)

    def test_numero_repetido_no_mesmo_tipo_e_barrado(self):
        self._envio(numero="4471")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._envio(numero="4471", quantidade_pecas=50)

    def test_mesmo_numero_em_tipos_diferentes_convive(self):
        """OSE e OSI são numeradas em séries separadas no ERP — 4471 pode
        existir nas duas sem ser duplicata."""
        self._envio(tipo=EnvioProducao.Tipo.OSE, numero="4471")
        self._envio(tipo=EnvioProducao.Tipo.OSI, numero="4471", quantidade_pecas=50)
        self.assertEqual(EnvioProducao.objects.filter(numero="4471").count(), 2)

    def test_varias_os_sem_numero_convivem(self):
        """A restrição é condicional: vazio não é duplicata de vazio, senão
        só caberia uma remessa sem OS emitida por vez."""
        self._envio()
        self._envio(quantidade_pecas=50)
        self.assertEqual(EnvioProducao.objects.filter(numero="").count(), 2)

    def test_os_sem_numero_vira_pendencia_no_rollup(self):
        self._envio(numero="4471")
        self._envio(quantidade_pecas=100)
        producao = calcular_producao(self.programacao)
        self.assertEqual(producao.enviado_pecas, 300)
        self.assertEqual(producao.envios_sem_numero, 1)

    def test_sem_pendencia_quando_toda_os_tem_numero(self):
        self._envio(numero="4471")
        self.assertEqual(calcular_producao(self.programacao).envios_sem_numero, 0)


class TipoOSSugeridoTests(TestCase):
    def test_costura_interna_sugere_osi(self):
        self.assertEqual(tipo_os_sugerido("COSTURA INTERNA"), EnvioProducao.Tipo.OSI)
        # normalize_text cuida de caixa/acento/espaço extra.
        self.assertEqual(tipo_os_sugerido("costura  interna"), EnvioProducao.Tipo.OSI)

    def test_faccao_sugere_ose(self):
        self.assertEqual(tipo_os_sugerido("MEGA BARIRI"), EnvioProducao.Tipo.OSE)
        self.assertEqual(tipo_os_sugerido(""), EnvioProducao.Tipo.OSE)


class EnvioProducaoFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def test_tipo_nasce_pre_selecionado_pelo_destino_da_op(self):
        interna = _programacao(self.user, destino_costura="COSTURA INTERNA")
        self.assertEqual(
            EnvioProducaoForm(programacao=interna).initial["tipo"], EnvioProducao.Tipo.OSI)
        self.assertEqual(
            EnvioProducaoForm(programacao=_programacao(self.user, pedido="999")).initial["tipo"],
            EnvioProducao.Tipo.OSE)

    def test_numero_e_normalizado(self):
        programacao = _programacao(self.user)
        form = EnvioProducaoForm(
            {"data": "2026-09-01", "tipo": "OSE", "numero": " os-4471 ",
             "destino": "MEGA BARIRI", "quantidade_pecas": "200", "observacao": ""},
            programacao=programacao)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["numero"], "OS-4471")

    def test_numero_repetido_vira_erro_de_validacao_e_nao_erro_500(self):
        programacao = _programacao(self.user)
        EnvioProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=200, criado_por=self.user, tipo="OSE", numero="4471")
        form = EnvioProducaoForm(
            {"data": "2026-09-02", "tipo": "OSE", "numero": "4471",
             "destino": "MEGA BARIRI", "quantidade_pecas": "100", "observacao": ""},
            programacao=programacao)
        self.assertFalse(form.is_valid())
        self.assertIn("Já existe uma OS com este tipo e número.",
                      " ".join(m for erros in form.errors.values() for m in erros))


# STORAGES: em produção o Whitenoise serve estático com hash no nome, e o
# manifesto só existe depois do collectstatic — sem esta troca, qualquer
# teste que RENDERIZE uma página quebra no {% static %}, não no que ele veio
# testar.
@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class RegistrarEnvioViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)
        self.programacao = _programacao(self.user)

    def _post(self, **campos):
        dados = {"data": "2026-09-01", "tipo": "OSE", "numero": "4471",
                 "destino": "MEGA BARIRI", "quantidade_pecas": "200", "observacao": ""}
        dados.update(campos)
        return self.client.post(
            reverse("controle_op:registrar_envio", args=[self.programacao.id]), dados)

    def test_grava_a_os_pela_tela(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        envio = EnvioProducao.objects.get()
        self.assertEqual((envio.tipo, envio.numero), ("OSE", "4471"))

    def test_numero_repetido_nao_grava_e_explica_o_motivo(self):
        self._post()
        resp = self._post(quantidade_pecas="100")
        self.assertEqual(EnvioProducao.objects.count(), 1)
        mensagens = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any("Já existe uma OS" in m for m in mensagens), mensagens)

    def test_envio_sem_numero_grava_e_aparece_como_pendencia_no_detalhe(self):
        self._post(numero="")
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.programacao.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["producao"].envios_sem_numero, 1)
        etapa_envio = next(e for e in resp.context["etapas"] if e["nome"] == "Envio")
        self.assertFalse(etapa_envio["ok"])
        self.assertIn("sem número", etapa_envio["sub"])


class FechamentoPdfTests(TestCase):
    """A coluna de OS entrou na tabela de envios do PDF — `_larguras_auto`
    reparte a página pelo número de colunas, então errar a conta aqui só
    apareceria na hora de imprimir."""

    def test_pdf_sai_com_a_coluna_de_os(self):
        user = get_user_model().objects.create_superuser("pcp", password="x")
        programacao = _programacao(user)
        EnvioProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=200, criado_por=user, tipo="OSE", numero="4471")
        EnvioProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2), destino="MEGA BARIRI",
            quantidade_pecas=100, criado_por=user)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=180,
            qualidade_segunda_pecas=20, retalho_kg=Decimal("4.00"), criado_por=user)

        pdf = relatorio_pdf.gerar_pdf_fechamento(
            programacao=programacao,
            aproveitamento=calcular_aproveitamento(programacao),
            registros=[],
            producao=calcular_producao(programacao),
            acumulada=producao_por_op(programacao),
            envios=list(programacao.envios_producao.order_by("data")),
            retornos=[],
        )
        self.assertTrue(pdf.startswith(b"%PDF"))


class RegistroProducaoTests(TestCase):
    """Fase 2 — o apontamento de produção, o módulo que faltava entre Envio e
    Retorno."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)
        EnvioProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=500, criado_por=self.user, tipo="OSE", numero="4471")

    def _apontar(self, **campos) -> RegistroProducao:
        dados = dict(
            programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=100,
            criado_por=self.user)
        dados.update(campos)
        return RegistroProducao.objects.create(**dados)

    def test_varios_apontamentos_por_op_somam(self):
        self._apontar(quantidade_pecas=100)
        self._apontar(data=date(2026, 9, 3), quantidade_pecas=150)
        acumulada = producao_por_op(self.programacao)
        self.assertEqual(acumulada.apontamentos, 2)
        self.assertEqual(acumulada.produzido_1a_total, 250)
        self.assertEqual(acumulada.produzido_total, 250)

    def test_segunda_qualidade_conta_como_entregue_e_fica_rastreada(self):
        self._apontar(quantidade_pecas=90, qualidade_segunda_pecas=10)
        acumulada = producao_por_op(self.programacao)
        self.assertEqual(acumulada.produzido_1a_total, 90)
        self.assertEqual(acumulada.produzido_2a_total, 10)
        self.assertEqual(acumulada.produzido_total, 100)

    def test_wip_envio_producao_e_o_que_esta_parado_na_faccao(self):
        self._apontar(quantidade_pecas=200, qualidade_segunda_pecas=20)
        self.assertEqual(producao_por_op(self.programacao).wip_envio_producao, 280)

    def test_wip_nunca_fica_negativo(self):
        """Apontar mais do que foi enviado é aviso (Fase 3), não impossível —
        mas "-50 peças na facção" não quer dizer nada na tela."""
        self._apontar(quantidade_pecas=600)
        self.assertEqual(producao_por_op(self.programacao).wip_envio_producao, 0)

    def test_retalho_ausente_fica_none_e_nao_zero(self):
        self._apontar()
        self.assertIsNone(producao_por_op(self.programacao).retalho_producao_kg_total)

    def test_retalho_soma_so_o_que_foi_pesado(self):
        self._apontar(retalho_kg=Decimal("12.50"))
        self._apontar(data=date(2026, 9, 3))
        self._apontar(data=date(2026, 9, 4), retalho_kg=Decimal("7.25"))
        self.assertAlmostEqual(
            producao_por_op(self.programacao).retalho_producao_kg_total, 19.75)

    def test_op_sem_apontamento_nenhum(self):
        acumulada = producao_por_op(self.programacao)
        self.assertFalse(acumulada.tem_apontamento)
        self.assertEqual(acumulada.produzido_total, 0)
        self.assertEqual(acumulada.wip_envio_producao, 500)

    def test_apontamento_nao_mexe_no_retorno(self):
        """Produção e Retorno são estágios distintos: apontar não faz a peça
        voltar fisicamente (a reconciliação entre os dois é a Fase 3)."""
        self._apontar(quantidade_pecas=500)
        self.assertEqual(calcular_producao(self.programacao).retornado_pecas, 0)


class RegistroProducaoFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")

    def _form(self, **campos):
        dados = {"data": "2026-09-02", "quantidade_pecas": "100",
                 "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""}
        dados.update(campos)
        return RegistroProducaoForm(dados)

    def test_apontamento_zerado_e_recusado(self):
        form = self._form(quantidade_pecas="0", qualidade_segunda_pecas="0")
        self.assertFalse(form.is_valid())
        self.assertIn("Lance ao menos uma peça",
                      " ".join(m for erros in form.errors.values() for m in erros))

    def test_so_segunda_qualidade_e_apontamento_valido(self):
        self.assertTrue(self._form(quantidade_pecas="0", qualidade_segunda_pecas="5").is_valid())

    def test_retalho_vazio_passa_como_none(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["retalho_kg"])


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class RegistrarProducaoViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)
        self.programacao = _programacao(self.user)
        EnvioProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=500, criado_por=self.user, tipo="OSE", numero="4471")

    def _post(self, **campos):
        dados = {"data": "2026-09-02", "quantidade_pecas": "200",
                 "qualidade_segunda_pecas": "20", "retalho_kg": "5.5", "observacao": ""}
        dados.update(campos)
        return self.client.post(
            reverse("controle_op:registrar_producao", args=[self.programacao.id]), dados)

    def test_grava_o_apontamento_pela_tela(self):
        self.assertEqual(self._post().status_code, 302)
        registro = RegistroProducao.objects.get()
        self.assertEqual(registro.total_pecas, 220)
        self.assertEqual(registro.criado_por, self.user)

    def test_apontamento_zerado_nao_grava_e_explica(self):
        resp = self._post(quantidade_pecas="0", qualidade_segunda_pecas="0")
        self.assertEqual(RegistroProducao.objects.count(), 0)
        mensagens = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any("Lance ao menos uma peça" in m for m in mensagens), mensagens)

    def test_detalhe_mostra_o_apontado_e_o_que_falta_na_faccao(self):
        self._post()
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.programacao.id]))
        self.assertEqual(resp.status_code, 200)
        acumulada = resp.context["acumulada"]
        self.assertEqual(acumulada.produzido_total, 220)
        self.assertEqual(acumulada.wip_envio_producao, 280)

        etapa = next(e for e in resp.context["etapas"] if e["nome"] == "Produção")
        self.assertFalse(etapa["ok"])
        self.assertIn("na facção", etapa["sub"])

    def test_etapa_producao_fecha_quando_a_faccao_aponta_o_que_recebeu(self):
        self._post(quantidade_pecas="500", qualidade_segunda_pecas="0")
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.programacao.id]))
        etapa = next(e for e in resp.context["etapas"] if e["nome"] == "Produção")
        self.assertTrue(etapa["ok"])


class RetornoReconciliacaoTests(TestCase):
    """Fase 3 — o Retorno reconcilia contra o que a Produção apontou, não
    contra o programado. É o fix do bug que travava OP com corte parcial
    legítimo pra sempre."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        # qnt_programada bem maior que o cortado/enviado de propósito —
        # simula o corte parcial legítimo que travava antes do fix.
        self.programacao = _programacao(self.user, qnt_programada=1000)

    def _envio(self, quantidade=600):
        EnvioProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=quantidade, criado_por=self.user, tipo="OSE", numero="4471")

    def _producao(self, quantidade=600):
        RegistroProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=quantidade,
            criado_por=self.user)

    def _retorno(self, quantidade):
        RetornoProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 3), quantidade_pecas=quantidade,
            criado_por=self.user)

    def _producao_calculada(self):
        acumulada = producao_por_op(self.programacao)
        return calcular_producao(self.programacao, produzido_total=acumulada.produzido_total)

    def test_o_bug_corte_parcial_fecha_quando_retorno_bate_o_produzido(self):
        """600/1000 (60%) nunca batia o LIMIAR_CONCLUIDO contra o programado
        — agora a régua é 600/600 (100%), contra o que a Produção apontou."""
        self._envio(600)
        self._producao(600)
        self._retorno(600)
        self.assertEqual(self._producao_calculada().status, StatusProducao.CONCLUIDO)

    def test_retorno_abaixo_do_produzido_fica_pendente(self):
        self._envio(600)
        self._producao(600)
        self._retorno(500)  # 500/600 = 83% < 96%
        self.assertEqual(self._producao_calculada().status, StatusProducao.EM_INDUSTRIALIZACAO)

    def test_sem_produzido_ainda_cai_pro_enviado_como_alvo_provisorio(self):
        """Sem nenhum apontamento de Produção lançado, a OP não pode ficar
        travada em NAO_INICIADO só porque uma etapa anterior está vazia —
        usa o enviado como alvo provisório até a Produção ser apontada."""
        self._envio(600)
        self._retorno(600)
        self.assertEqual(self._producao_calculada().status, StatusProducao.CONCLUIDO)

    def test_saldo_a_retornar_e_contra_o_produzido_nao_contra_o_enviado(self):
        self._envio(800)
        self._producao(600)
        self._retorno(400)
        producao = self._producao_calculada()
        self.assertEqual(producao.saldo_a_retornar, 200)  # 600 - 400, não 800 - 400

    def test_saldo_a_retornar_nunca_fica_negativo(self):
        self._envio(600)
        self._producao(600)
        self._retorno(650)  # retornou mais que o apontado
        self.assertEqual(self._producao_calculada().saldo_a_retornar, 0)

    def test_calcular_producao_sem_produzido_total_usa_enviado_como_fallback(self):
        """Chamada sem o kwarg (compatibilidade) não deve mais usar
        qnt_programada — cai pro enviado, igual ao alvo provisório acima."""
        self._envio(600)
        self._retorno(600)
        self.assertEqual(calcular_producao(self.programacao).status, StatusProducao.CONCLUIDO)


class RetornoProducaoFormWarningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user, qnt_programada=1000)
        EnvioProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=600, criado_por=self.user, tipo="OSE", numero="4471")
        RegistroProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=600,
            criado_por=self.user)

    def _form(self, quantidade):
        return RetornoProducaoForm(
            {"data": "2026-09-03", "quantidade_pecas": str(quantidade),
             "retalho_kg": "", "observacao": ""},
            programacao=self.programacao)

    def test_retorno_dentro_do_produzido_nao_avisa(self):
        form = self._form(600)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(getattr(form, "add_warning", None))

    def test_retorno_acima_do_produzido_avisa_mas_nao_bloqueia(self):
        form = self._form(650)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(getattr(form, "add_warning", None))
        self.assertIn("650", form.add_warning)


class RegistroProducaoFormWarningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)

    def _form(self, quantidade):
        return RegistroProducaoForm(
            {"data": "2026-09-02", "quantidade_pecas": str(quantidade),
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""},
            programacao=self.programacao)

    def test_sem_nenhum_envio_avisa(self):
        form = self._form(100)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(getattr(form, "add_warning", None))
        self.assertIn("envio", form.add_warning.lower())

    def test_producao_acima_do_enviado_avisa_mas_nao_bloqueia(self):
        EnvioProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=100, criado_por=self.user, tipo="OSE", numero="4471")
        form = self._form(150)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(getattr(form, "add_warning", None))

    def test_producao_dentro_do_enviado_nao_avisa(self):
        EnvioProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=100, criado_por=self.user, tipo="OSE", numero="4471")
        form = self._form(80)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(getattr(form, "add_warning", None))


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class RegistrarRetornoViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)
        self.programacao = _programacao(self.user, qnt_programada=1000)
        EnvioProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=600, criado_por=self.user, tipo="OSE", numero="4471")
        RegistroProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=600,
            criado_por=self.user)

    def _post(self, quantidade):
        return self.client.post(
            reverse("controle_op:registrar_retorno", args=[self.programacao.id]),
            {"data": "2026-09-03", "quantidade_pecas": str(quantidade),
             "retalho_kg": "", "observacao": ""})

    def test_retorno_completo_fecha_a_op_apesar_do_corte_parcial(self):
        self._post(600)
        resp = self.client.get(reverse("controle_op:detalhe", args=[self.programacao.id]))
        etapa = next(e for e in resp.context["etapas"] if e["nome"] == "Retorno")
        self.assertTrue(etapa["ok"])
        self.assertEqual(resp.context["producao"].status, StatusProducao.CONCLUIDO)

    def test_retorno_acima_do_produzido_grava_e_avisa_na_tela(self):
        resp = self._post(650)
        self.assertEqual(RetornoProducao.objects.count(), 1)
        mensagens = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any("650" in m for m in mensagens), mensagens)


class ListaOPTests(TestCase):
    """A lista de OPs (`controle_op:lista`) também reconcilia contra o
    produzido — sem isso `fechado_geral` ficaria inconsistente com o que a
    tela de detalhe mostra pra mesma OP."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.client.force_login(self.user)

    @override_settings(
        ROOT_URLCONF="controle_op.test_urls",
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    def test_lista_nao_erro_e_reflete_o_status_do_retorno(self):
        programacao = _programacao(self.user, qnt_programada=1000,
                                   status=ProgramacaoCorte.Status.CONCLUIDO)
        EnvioProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 1), destino="MEGA BARIRI",
            quantidade_pecas=600, criado_por=self.user, tipo="OSE", numero="4471")
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 2), quantidade_pecas=600,
            criado_por=self.user)
        RetornoProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 3), quantidade_pecas=600,
            criado_por=self.user)

        resp = self.client.get(reverse("controle_op:lista"))
        self.assertEqual(resp.status_code, 200)
        item = next(i for i in resp.context["itens"] if i["programacao"].id == programacao.id)
        self.assertEqual(item["producao"].status, StatusProducao.CONCLUIDO)
