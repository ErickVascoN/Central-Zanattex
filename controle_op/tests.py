"""Fase 1 da Gestão de OP — o envio vira a OS do ERP (OSE/OSI + número), que
é o vínculo que o ERP não tem entre pedido, corte e industrialização."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.aproveitamento import calcular_aproveitamento
from corte.models import ProgramacaoCorte

from . import relatorio_pdf
from .forms import EnvioProducaoForm
from .models import EnvioProducao, tipo_os_sugerido
from .producao import calcular_producao


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

        pdf = relatorio_pdf.gerar_pdf_fechamento(
            programacao=programacao,
            aproveitamento=calcular_aproveitamento(programacao),
            registros=[],
            producao=calcular_producao(programacao),
            envios=list(programacao.envios_producao.order_by("data")),
            retornos=[],
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
