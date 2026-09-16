"""Fase 2b — link do prestador, sem login. O escopo de acesso vem do token
do `Prestador`, não de sessão: as duas telas (`prestador_lista`,
`prestador_op`) precisam funcionar pra um client SEM `force_login`, e nunca
mostrar/aceitar apontamento de OP que não seja daquele prestador."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import UnidadeCorte
from corte.models import ProgramacaoCorte

from .admin import PrestadorAdminForm
from .forms import RegistroProducaoPrestadorForm
from .models import EnvioProducao, MetaPrestador, Prestador, RegistroProducao


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
    # numero precisa ser único (tipo, numero) — inclui o id da OP pra não
    # colidir quando o mesmo prestador recebe envio de OPs diferentes.
    return EnvioProducao.objects.create(
        programacao=programacao, data=date(2026, 9, 1), destino=destino,
        quantidade_pecas=quantidade, criado_por=user, tipo="OSE",
        numero=f"OS-{programacao.pedido}-{destino}")


class PrestadorModelTests(TestCase):
    def test_token_gerado_sozinho_e_unico(self):
        a = Prestador.objects.create(nome="MEGA BARIRI")
        b = Prestador.objects.create(nome="ZARO (LUIS)")
        self.assertTrue(a.token)
        self.assertNotEqual(a.token, b.token)

    def test_nome_e_unico(self):
        Prestador.objects.create(nome="MEGA BARIRI")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Prestador.objects.create(nome="MEGA BARIRI")


class PrestadorNomeTravadoNaListaTests(TestCase):
    """Nome livre deixaria passar um nome quase-certo que nunca casaria com
    EnvioProducao.destino, silenciosamente — o form do admin trava numa
    lista fechada; `clean()` trava de novo pra qualquer outro caminho."""

    @patch("controle_op.models.opcoes_prestador", return_value=["MEGA BARIRI", "ZARO (LUIS)"])
    def test_nome_fora_da_lista_e_recusado_no_clean(self, _mock):
        p = Prestador(nome="Mega Bariri Ltda")  # quase certo, mas não é o nome exato
        with self.assertRaises(Exception):
            p.full_clean()

    @patch("controle_op.models.opcoes_prestador", return_value=["MEGA BARIRI", "ZARO (LUIS)"])
    def test_nome_da_lista_passa_no_clean(self, _mock):
        p = Prestador(nome="MEGA BARIRI")
        p.full_clean()  # não levanta

    @patch("controle_op.models.opcoes_prestador", return_value=["MEGA BARIRI", "ZARO (LUIS)"])
    def test_admin_form_so_oferece_a_lista_fechada(self, _mock):
        form = PrestadorAdminForm()
        valores = [v for v, _ in form.fields["nome"].widget.choices]
        self.assertIn("MEGA BARIRI", valores)
        self.assertIn("ZARO (LUIS)", valores)
        self.assertNotIn("COSTURA INTERNA", valores)

    @patch("controle_op.models.opcoes_prestador", return_value=["MEGA BARIRI"])
    def test_admin_form_recusa_nome_fora_da_lista(self, _mock):
        form = PrestadorAdminForm({"nome": "ZARO (LUIS)", "telefone": "", "ativo": "on"})
        self.assertFalse(form.is_valid())

    @patch("controle_op.models.opcoes_prestador", return_value=["MEGA BARIRI"])
    def test_editar_registro_com_nome_fora_da_lista_atual_nao_trava(self, _mock):
        """A facção pode ter saído da planilha viva depois de cadastrada —
        editar outros campos (ex.: telefone) não pode travar por causa
        disso; a opção antiga some da lista, não do registro."""
        p = Prestador.objects.create(nome="PRESTADOR ANTIGO")
        form = PrestadorAdminForm(
            {"nome": "PRESTADOR ANTIGO", "telefone": "5514999998888", "ativo": "on"},
            instance=p)
        self.assertTrue(form.is_valid(), form.errors)


class RegistroProducaoPrestadorFormTests(TestCase):
    def _dados(self, **over):
        dados = {"criado_por_nome": "João", "data": "2026-09-02", "quantidade_pecas": "100",
                 "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""}
        dados.update(over)
        return dados

    def test_form_valido_grava_sem_criado_por(self):
        form = RegistroProducaoPrestadorForm(self._dados())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("destino", form.fields)  # não é campo — a view escreve sozinha

    def test_apontamento_zerado_e_recusado(self):
        form = RegistroProducaoPrestadorForm(
            self._dados(quantidade_pecas="0", qualidade_segunda_pecas="0"))
        self.assertFalse(form.is_valid())

    def test_nome_obrigatorio(self):
        form = RegistroProducaoPrestadorForm(self._dados(criado_por_nome=""))
        self.assertFalse(form.is_valid())
        self.assertIn("criado_por_nome", form.errors)


class RegistroProducaoConstraintTests(TestCase):
    """A trava do banco (não só a da view/form) — um apontamento sempre tem
    alguém por trás, nunca os dois vazios nem os dois preenchidos."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.programacao = _programacao(self.user)

    def test_interno_sem_criado_por_e_recusado_pelo_banco(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistroProducao.objects.create(
                programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=10,
                origem=RegistroProducao.Origem.INTERNO, criado_por=None)

    def test_prestador_com_criado_por_e_recusado_pelo_banco(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistroProducao.objects.create(
                programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=10,
                origem=RegistroProducao.Origem.PRESTADOR, criado_por=self.user,
                criado_por_nome="João")

    def test_prestador_sem_nome_e_recusado_pelo_banco(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistroProducao.objects.create(
                programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=10,
                origem=RegistroProducao.Origem.PRESTADOR, criado_por=None, criado_por_nome="")

    def test_prestador_valido_grava(self):
        registro = RegistroProducao.objects.create(
            programacao=self.programacao, data=date(2026, 9, 2), quantidade_pecas=10,
            origem=RegistroProducao.Origem.PRESTADOR, criado_por=None, criado_por_nome="João")
        self.assertIsNone(registro.criado_por)


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class PrestadorListaViewTests(TestCase):
    """Client SEM login — é assim que a facção acessa de verdade."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.mega = Prestador.objects.create(nome="MEGA BARIRI")
        self.zaro = Prestador.objects.create(nome="ZARO (LUIS)")

    def test_sem_login_funciona(self):
        resp = self.client.get(reverse("controle_op:prestador_lista", args=[self.mega.token]))
        self.assertEqual(resp.status_code, 200)

    def test_token_invalido_da_404(self):
        resp = self.client.get(reverse("controle_op:prestador_lista", args=["token-que-nao-existe"]))
        self.assertEqual(resp.status_code, 404)

    def test_prestador_inativo_da_404(self):
        self.mega.ativo = False
        self.mega.save()
        resp = self.client.get(reverse("controle_op:prestador_lista", args=[self.mega.token]))
        self.assertEqual(resp.status_code, 404)

    def test_lista_so_as_ops_do_prestador_com_saldo(self):
        p1 = _programacao(self.user, pedido="111")
        _envio(p1, self.user, "MEGA BARIRI", 100)
        p2 = _programacao(self.user, pedido="222")
        _envio(p2, self.user, "ZARO (LUIS)", 50)  # de outro prestador
        p3 = _programacao(self.user, pedido="333")
        _envio(p3, self.user, "MEGA BARIRI", 80)
        RegistroProducao.objects.create(
            programacao=p3, data=date(2026, 9, 2), quantidade_pecas=80,
            destino="MEGA BARIRI", criado_por=self.user)
        from .models import RetornoProducao
        RetornoProducao.objects.create(
            programacao=p3, data=date(2026, 9, 4), quantidade_pecas=80,
            destino="MEGA BARIRI", criado_por=self.user)  # já fechou

        resp = self.client.get(reverse("controle_op:prestador_lista", args=[self.mega.token]))
        ids = [p.id for p, _ in resp.context["abertas"]]
        self.assertIn(p1.id, ids)       # tem saldo em aberto
        self.assertNotIn(p2.id, ids)    # é de outro prestador
        self.assertNotIn(p3.id, ids)    # já fechou (saldo zerado)

    def test_visitante_logado_tambem_ve_o_conteudo(self):
        """base.html renderiza {% block content %} (dentro da sidebar) pra
        quem está autenticado, e {% block content_anon %} só pra quem não
        está — o próprio time testando o link na sessão logada da Central
        não pode cair numa página em branco (V: bug real, achado ao vivo)."""
        p1 = _programacao(self.user, pedido="111")
        _envio(p1, self.user, "MEGA BARIRI", 100)
        self.client.force_login(self.user)
        resp = self.client.get(reverse("controle_op:prestador_lista", args=[self.mega.token]))
        self.assertContains(resp, "OP 111")


@override_settings(
    ROOT_URLCONF="controle_op.test_urls",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class PrestadorOpViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pcp", password="x")
        self.mega = Prestador.objects.create(nome="MEGA BARIRI")
        self.zaro = Prestador.objects.create(nome="ZARO (LUIS)")
        self.programacao = _programacao(self.user)
        _envio(self.programacao, self.user, "MEGA BARIRI", 200)

    def test_op_de_outro_prestador_da_404(self):
        """Trocar o número da URL não abre o apontamento de uma OP que não
        é do prestador do token."""
        resp = self.client.get(
            reverse("controle_op:prestador_op", args=[self.zaro.token, self.programacao.id]))
        self.assertEqual(resp.status_code, 404)

    def test_get_mostra_o_formulario_e_o_saldo(self):
        resp = self.client.get(
            reverse("controle_op:prestador_op", args=[self.mega.token, self.programacao.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["saldo"].enviado_pecas, 200)

    def test_falta_apontar_e_enviado_menos_produzido_nao_saldo_a_retornar(self):
        """Achado ao vivo: a tela mostrava `saldo.saldo_a_retornar` (produzido
        − retornado — quanto falta VOLTAR fisicamente) rotulado como "Falta
        apontar" (deveria ser enviado − produzido — quanto falta REGISTRAR).
        Enviado 1500, já apontou 1350 (1250 1ª + 100 2ª) → falta apontar 150,
        não 1350 (que só bateria por coincidência de retornado ser 0)."""
        programacao = _programacao(self.user, pedido="999")
        _envio(programacao, self.user, "MEGA BARIRI", 1500)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 15), quantidade_pecas=1250,
            qualidade_segunda_pecas=100, destino="MEGA BARIRI", criado_por=self.user)
        resp = self.client.get(
            reverse("controle_op:prestador_op", args=[self.mega.token, programacao.id]))
        self.assertEqual(resp.context["saldo"].enviado_pecas, 1500)
        self.assertEqual(resp.context["saldo"].produzido_pecas, 1350)
        self.assertEqual(resp.context["falta_apontar"], 150)

    def test_falta_apontar_nunca_fica_negativo(self):
        """Apontou mais do que foi enviado (correção, produção extra etc.) —
        "falta apontar" não pode virar número negativo, vira 0."""
        programacao = _programacao(self.user, pedido="998")
        _envio(programacao, self.user, "MEGA BARIRI", 100)
        RegistroProducao.objects.create(
            programacao=programacao, data=date(2026, 9, 15), quantidade_pecas=150,
            qualidade_segunda_pecas=0, destino="MEGA BARIRI", criado_por=self.user)
        resp = self.client.get(
            reverse("controle_op:prestador_op", args=[self.mega.token, programacao.id]))
        self.assertEqual(resp.context["falta_apontar"], 0)

    def test_post_grava_registro_como_prestador(self):
        resp = self.client.post(
            reverse("controle_op:prestador_op", args=[self.mega.token, self.programacao.id]),
            {"criado_por_nome": "João da Mega", "data": "2026-09-02", "quantidade_pecas": "150",
             "qualidade_segunda_pecas": "10", "retalho_kg": "", "observacao": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["sucesso"])

        registro = RegistroProducao.objects.get()
        self.assertEqual(registro.origem, RegistroProducao.Origem.PRESTADOR)
        self.assertIsNone(registro.criado_por)
        self.assertEqual(registro.criado_por_nome, "João da Mega")
        self.assertEqual(registro.destino, "MEGA BARIRI")
        self.assertEqual(registro.total_pecas, 160)

    def test_post_invalido_nao_grava(self):
        resp = self.client.post(
            reverse("controle_op:prestador_op", args=[self.mega.token, self.programacao.id]),
            {"criado_por_nome": "", "data": "2026-09-02", "quantidade_pecas": "0",
             "qualidade_segunda_pecas": "0", "retalho_kg": "", "observacao": ""})
        self.assertEqual(RegistroProducao.objects.count(), 0)
        self.assertFalse(resp.context["sucesso"])

    def test_visitante_logado_tambem_ve_o_formulario(self):
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("controle_op:prestador_op", args=[self.mega.token, self.programacao.id]))
        self.assertContains(resp, "Apontar produção de hoje")


class MetaPrestadorTests(TestCase):
    """Captura de meta por prestador × produto (× cliente, quando a meta é
    específica de um cliente) — mesma granularidade da aba "METAS" da
    planilha real, editável no admin. Por ora só guarda o dado; não
    alimenta o dashboard de Metas × Realizado ainda (ver docstring do
    model)."""

    def setUp(self):
        self.prestador = Prestador.objects.create(nome="MEGA BARIRI")

    def test_cria_meta_sem_cliente(self):
        """A maioria das linhas da aba real não tem cliente preenchido —
        a meta vale pro prestador+produto em geral."""
        meta = MetaPrestador.objects.create(
            prestador=self.prestador, produto="MANTA", meta_pecas=7000)
        self.assertEqual(meta.prestador, self.prestador)
        self.assertEqual(meta.cliente, "")
        self.assertIn(meta, self.prestador.metas.all())

    def test_cria_meta_com_cliente(self):
        meta = MetaPrestador.objects.create(
            prestador=self.prestador, produto="MANTA", cliente="BURDAYS", meta_pecas=9090)
        self.assertEqual(meta.cliente, "BURDAYS")

    def test_mesmo_prestador_produto_cliente_duas_vezes_e_recusado(self):
        MetaPrestador.objects.create(
            prestador=self.prestador, produto="MANTA", cliente="BURDAYS", meta_pecas=9090)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MetaPrestador.objects.create(
                prestador=self.prestador, produto="MANTA", cliente="BURDAYS", meta_pecas=1000)

    def test_mesmo_produto_com_e_sem_cliente_convive(self):
        """"MANTA" sem cliente e "MANTA" pra um cliente específico são
        metas diferentes — a aba real tem esse caso (GIATTEX/MANTA aparece
        várias vezes com clientes diferentes)."""
        MetaPrestador.objects.create(
            prestador=self.prestador, produto="MANTA", meta_pecas=7000)
        MetaPrestador.objects.create(
            prestador=self.prestador, produto="MANTA", cliente="BURDAYS", meta_pecas=9090)
        self.assertEqual(self.prestador.metas.count(), 2)

    def test_mesmo_prestador_produtos_diferentes_convive(self):
        MetaPrestador.objects.create(
            prestador=self.prestador, produto="MANTA", meta_pecas=7000)
        MetaPrestador.objects.create(
            prestador=self.prestador, produto="FRONHA AVULSA", meta_pecas=1000)
        self.assertEqual(self.prestador.metas.count(), 2)

    def test_apagar_prestador_apaga_as_metas_junto(self):
        MetaPrestador.objects.create(
            prestador=self.prestador, produto="MANTA", meta_pecas=7000)
        self.prestador.delete()
        self.assertEqual(MetaPrestador.objects.count(), 0)
