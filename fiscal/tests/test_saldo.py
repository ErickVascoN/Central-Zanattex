"""Regras do Saldo Fiscal que a auditoria de set/2026 achou quebradas — cada
teste é um caso real que distorcia o saldo apresentado ao cliente (nota
anulada por estorno baixando, nota sem autorização baixando, item zerado
virando "sem correspondente", baixa dupla na resolução manual, KG somado com
MT...). XMLs sintéticos, mínimos, no formato da NF-e."""
from __future__ import annotations

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from fiscal import importador, matching, sefaz_servico, servicos
from fiscal.models import Cliente, NotaFiscal, NotaFiscalItem, PendenciaMatching, Vinculo
from fiscal.sefaz import ResultadoConsultaSefaz
from fiscal.sefaz import Situacao as SefazSituacao

ZAN = "14601572000130"
CLI = "43672716000148"


def _xml(n: int, *, emit: str, dest: str, itens: list[dict], tp_nf: str = "1", inf_cpl: str = "",
         cstat: str | None = "100", dh: str = "2026-09-01T10:00:00-03:00") -> bytes:
    chave = f"{n:0>20}{emit}{tp_nf}{'0' * 9}"
    dets = "".join(
        f'<det nItem="{i}"><prod><cProd>{it["cprod"]}</cProd><xProd>{it["xprod"]}</xProd>'
        f'<NCM>{it.get("ncm", "60019200")}</NCM><CFOP>{it["cfop"]}</CFOP><uCom>{it.get("ucom", "KG")}</uCom>'
        f'<qCom>{it["q"]}</qCom><vUnCom>{it.get("vun", "10")}</vUnCom><vProd>0</vProd></prod>'
        f'{"<infAdProd>" + it["ref"] + "</infAdProd>" if it.get("ref") else ""}</det>'
        for i, it in enumerate(itens, 1))
    prot = (f'<protNFe><infProt><chNFe>{chave}</chNFe><cStat>{cstat}</cStat><xMotivo>ok</xMotivo>'
            f'</infProt></protNFe>' if cstat else "")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">'
        f'<NFe><infNFe Id="NFe{chave}"><ide><natOp>teste</natOp><serie>1</serie><nNF>{n}</nNF>'
        f'<dhEmi>{dh}</dhEmi><tpNF>{tp_nf}</tpNF></ide>'
        f'<emit><CNPJ>{emit}</CNPJ><xNome>EMIT {emit}</xNome></emit>'
        f'<dest><CNPJ>{dest}</CNPJ><xNome>DEST {dest}</xNome></dest>{dets}'
        f'<total><ICMSTot><vNF>0</vNF></ICMSTot></total>'
        f'<infAdic><infCpl>{inf_cpl}</infCpl></infAdic></infNFe></NFe>{prot}</nfeProc>'
    ).encode()


def _entrada(n, itens, **kw):
    return _xml(n, emit=CLI, dest=ZAN, itens=[{"cfop": "5901", **i} for i in itens], **kw)


def _saida(n, itens, **kw):
    return _xml(n, emit=ZAN, dest=CLI, itens=[{"cfop": "5902", **i} for i in itens], **kw)


FLEECE = {"cprod": "F1", "xprod": "TECIDO CORAL FLEECE LISO"}


@override_settings(FISCAL_NCMS_CONTROLADOS={"60019200", "54075210", "54075400"})
class SaldoFiscalTests(TestCase):
    def setUp(self):
        Cliente.objects.create(nome="Camesa", cnpj=CLI)
        # CentroCusto do CNPJ ZAN já vem seedado pela migração 0010 (mesmo
        # CNPJ que era hardcoded em FISCAL_CNPJS_ZANATTEX) — nada a criar aqui.

    def importar(self, xml: bytes) -> NotaFiscal:
        r = importador.confirmar_importacao(xml, "x.xml")
        self.assertEqual(r.status, "importada")
        return r.nota

    def item_entrada(self, n_nf, n_item=1) -> NotaFiscalItem:
        return NotaFiscalItem.objects.get(
            nota_fiscal__n_nf=str(n_nf), nota_fiscal__tipo="ENTRADA", n_item=n_item)

    # ── o caminho feliz: NF de referência -> baixa no produto ──────────────
    def test_saida_baixa_o_saldo_do_produto_da_nf_citada(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("70"))

    # ── estorno (NF de entrada própria, tpNF=0) ─────────────────────────────
    def test_estorno_anula_a_saida_e_devolve_o_saldo(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        estorno = self.importar(_xml(2, emit=ZAN, dest=CLI, tp_nf="0", inf_cpl="ENTRADA REF NF 1;|x",
                                     itens=[{**FLEECE, "cfop": "1949", "q": "30", "ref": "100"}]))
        saida.refresh_from_db()
        self.assertEqual(estorno.situacao, NotaFiscal.Situacao.ESTORNO)
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.ANULADA)
        self.assertEqual(saida.anulada_por, estorno)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("100"))
        self.assertFalse(Vinculo.objects.exists())

    def test_estorno_importado_antes_da_saida_anula_quando_ela_chega(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.importar(_xml(2, emit=ZAN, dest=CLI, tp_nf="0", inf_cpl="ENTRADA REF NF 1",
                           itens=[{**FLEECE, "cfop": "1949", "q": "30"}]))
        self.assertTrue(PendenciaMatching.objects.filter(motivo="ESTORNO_NAO_CONFERE", resolvido=False).exists())
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        saida.refresh_from_db()
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.ANULADA)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("100"))
        self.assertFalse(PendenciaMatching.objects.filter(motivo="ESTORNO_NAO_CONFERE").exists())

    # ── autorização de uso ──────────────────────────────────────────────────
    def test_nota_sem_protocolo_nao_baixa(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}], cstat=None))
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.NAO_AUTORIZADA)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("100"))

    def test_cstat_150_autorizada_fora_de_prazo_vale(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}], cstat="150"))
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.VALIDA)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("70"))

    # ── item zerado: baixa nele mesmo e aparece como excedido ───────────────
    def test_item_ja_zerado_recebe_a_baixa_e_fica_excedido(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "60", "ref": "100"}]))
        self.importar(_saida(2, [{**FLEECE, "q": "60", "ref": "100"}], dh="2026-09-05T10:00:00-03:00"))
        item = self.item_entrada(100)
        self.assertEqual(item.saldo_atual, Decimal("-20"))
        self.assertFalse(PendenciaMatching.objects.filter(motivo="PRODUTO_SEM_CORRESPONDENTE").exists())
        linha = servicos.historico_itens(servicos.Filtros())[0]
        self.assertEqual(linha.status, "EXCEDIDO")
        self.assertEqual(linha.excedido, Decimal("20"))

    # ── "único item com saldo" não chuta quando há outro tecido sem saldo ──
    def test_unico_item_nao_absorve_tecido_de_outro_ncm(self):
        self.importar(_entrada(100, [
            {**FLEECE, "q": "100"},
            {"cprod": "M1", "xprod": "TEC MICROFIBRA EST", "ncm": "99999999", "ucom": "MT", "q": "500"},
        ]))
        self.importar(_saida(1, [{"cprod": "M1", "xprod": "TEC MICROFIBRA EST", "ncm": "54075210",
                                  "ucom": "MT", "q": "50", "ref": "100"}]))
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("100"))
        self.assertTrue(PendenciaMatching.objects.filter(motivo="PRODUTO_SEM_CORRESPONDENTE").exists())

    # ── duplicidade (cancelada na SEFAZ, evento fora do XML) ────────────────
    def test_duplicidade_marca_e_cancelar_desfaz_a_baixa(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}], dh="2026-09-01T10:00:00-03:00"))
        segunda = self.importar(_saida(2, [{**FLEECE, "q": "30", "ref": "100"}], dh="2026-09-01T10:20:00-03:00"))
        pend = PendenciaMatching.objects.get(motivo="POSSIVEL_DUPLICIDADE")
        self.assertEqual(pend.saida_item.nota_fiscal, segunda)
        self.assertEqual(list(pend.itens_entrada.all()), [self.item_entrada(100)])
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("40"))

        matching.marcar_cancelada(NotaFiscal.objects.get(n_nf="1", tipo="SAIDA"))
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("70"))
        pend.refresh_from_db()
        self.assertTrue(pend.resolvido)

    def test_saidas_iguais_com_mais_de_48h_nao_sao_duplicidade(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}], dh="2026-09-01T10:00:00-03:00"))
        self.importar(_saida(2, [{**FLEECE, "q": "30", "ref": "100"}], dh="2026-09-10T10:00:00-03:00"))
        self.assertFalse(PendenciaMatching.objects.filter(motivo="POSSIVEL_DUPLICIDADE").exists())

    # ── status por item, não por NF ─────────────────────────────────────────
    def test_divergencia_marca_so_o_item_afetado(self):
        self.importar(_entrada(100, [
            {**FLEECE, "q": "100"},
            {"cprod": "L1", "xprod": "TEC MICROFIBRA LISO", "ncm": "54075210", "ucom": "MT", "q": "500"},
        ]))
        # Saída do microfibra em KG: acha o item certo, mas a unidade não bate.
        self.importar(_saida(1, [{"cprod": "L1", "xprod": "TEC MICROFIBRA LISO", "ncm": "54075210",
                                  "ucom": "KG", "q": "5", "ref": "100"}]))
        status = {l.item.c_prod: l.status for l in servicos.historico_itens(servicos.Filtros())}
        self.assertEqual(status, {"F1": "NAO_UTILIZADO", "L1": "DIVERGENCIA"})

    # ── totais nunca somam KG com MT ────────────────────────────────────────
    def test_totais_separados_por_unidade(self):
        self.importar(_entrada(100, [
            {**FLEECE, "q": "100"},
            {"cprod": "L1", "xprod": "TEC MICROFIBRA LISO", "ncm": "54075210", "ucom": "MT", "q": "500"},
            {"cprod": "E1", "xprod": "ETIQUETA", "ncm": "58071000", "ucom": "UN", "q": "9999"},
        ]))
        totais = {t.unidade: t.recebido for t in servicos.kpis_dashboard().por_unidade}
        self.assertEqual(totais, {"KG": Decimal("100"), "MT": Decimal("500")})

    # ── resolução manual substitui a baixa, não soma outra ─────────────────
    def test_resolucao_manual_do_excesso_nao_baixa_duas_vezes(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "10"}]))
        self.importar(_entrada(101, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        pend = PendenciaMatching.objects.get(motivo="QUANTIDADE_EXCEDIDA")

        self.client.force_login(get_user_model().objects.create_superuser("adm", "a@a.com", "x"))
        r = self.client.post(reverse("fiscal:resolver_pendencia", args=[pend.pk]),
                             {"entrada_item_id": self.item_entrada(101).pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("10"))
        self.assertEqual(self.item_entrada(101).saldo_atual, Decimal("70"))
        self.assertEqual(Vinculo.objects.count(), 1)
        self.assertFalse(PendenciaMatching.objects.filter(resolvido=False).exists())

    # ── consulta ao SEFAZ no ato do import (fiscal/sefaz.py) ────────────────
    def test_nota_cancelada_na_sefaz_nasce_cancelada_sem_pendencia(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        xml = _saida(1, [{**FLEECE, "q": "30", "ref": "100"}])
        parsed = importador.parse_nfe(xml, "x.xml")
        resultado_sefaz = ResultadoConsultaSefaz(
            situacao=SefazSituacao.CANCELADA, cstat="101", xmotivo="Cancelamento homologado",
            protocolo="999")
        r = importador.confirmar_importacao_parsed(
            parsed, "x.xml", situacoes_sefaz={parsed.chave_acesso: resultado_sefaz})
        self.assertEqual(r.status, "importada")
        nota = r.nota
        self.assertEqual(nota.situacao, NotaFiscal.Situacao.CANCELADA)
        self.assertEqual(nota.cancelamento_origem, NotaFiscal.CancelamentoOrigem.IMPORTACAO)
        self.assertEqual(nota.protocolo_cancelamento, "999")
        self.assertIsNotNone(nota.situacao_sefaz_verificada_em)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("100"))  # não baixou
        self.assertFalse(PendenciaMatching.objects.exists())  # nasceu cancelada, sem fila de revisão

    def test_nota_autorizada_na_sefaz_segue_valida_e_baixa_normal(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        xml = _saida(1, [{**FLEECE, "q": "30", "ref": "100"}])
        parsed = importador.parse_nfe(xml, "x.xml")
        resultado_sefaz = ResultadoConsultaSefaz(situacao=SefazSituacao.AUTORIZADA, cstat="100")
        r = importador.confirmar_importacao_parsed(
            parsed, "x.xml", situacoes_sefaz={parsed.chave_acesso: resultado_sefaz})
        self.assertEqual(r.nota.situacao, NotaFiscal.Situacao.VALIDA)
        self.assertIsNotNone(r.nota.situacao_sefaz_verificada_em)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("70"))

    def test_sem_certificado_nao_verificada_importa_normal(self):
        # Sem FISCAL_SEFAZ_CERTIFICADOS_JSON (padrão do settings de teste),
        # sefaz.consultar_situacao devolve NAO_VERIFICADA sem bater na rede —
        # é o caminho que `self.importar` já exercita implicitamente em
        # todos os outros testes deste arquivo; aqui só deixa isso explícito.
        nota = self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.assertEqual(nota.situacao, NotaFiscal.Situacao.VALIDA)
        self.assertIsNone(nota.situacao_sefaz_verificada_em)
        self.assertEqual(nota.cancelamento_origem, "")

    def test_historico_filtro_situacao_mostra_entrada_cancelada_no_import(self):
        xml = _entrada(100, [{**FLEECE, "q": "100"}])
        parsed = importador.parse_nfe(xml, "x.xml")
        resultado_sefaz = ResultadoConsultaSefaz(situacao=SefazSituacao.CANCELADA, cstat="101", protocolo="999")
        r = importador.confirmar_importacao_parsed(
            parsed, "x.xml", situacoes_sefaz={parsed.chave_acesso: resultado_sefaz})
        self.assertEqual(r.nota.situacao, NotaFiscal.Situacao.CANCELADA)

        self.client.force_login(get_user_model().objects.create_superuser("adm7", "a7@a.com", "x"))
        # Sem filtro: some (comportamento de sempre, só VALIDA).
        r_padrao = self.client.get(reverse("fiscal:historico"), {"modo": "entrada"})
        self.assertNotContains(r_padrao, "Cancelada no import")
        # Com o filtro de Situação: aparece.
        r_filtrado = self.client.get(reverse("fiscal:historico"), {"modo": "entrada", "situacao": "CANCELADA"})
        self.assertContains(r_filtrado, "Cancelada no import")
        self.assertContains(r_filtrado, "TECIDO CORAL FLEECE LISO")

    # ── Fase 2: checagem periódica (fiscal/sefaz_servico.py) — rede de
    # segurança pra nota que já valia no saldo e só é cancelada depois ────
    def test_verificar_cancelamentos_cria_pendencia_sem_excluir_do_saldo(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))

        resultado_cancelada = ResultadoConsultaSefaz(
            situacao=SefazSituacao.CANCELADA, cstat="101", xmotivo="Cancelamento homologado", protocolo="777")
        with mock.patch("fiscal.sefaz_servico.sefaz.consultar_situacao_lote",
                        return_value={saida.chave_acesso: resultado_cancelada}):
            resumo = sefaz_servico.verificar_cancelamentos(chaves=[saida.chave_acesso])

        self.assertEqual(resumo.verificadas, 1)
        self.assertEqual(resumo.novas_para_revisao, 1)
        saida.refresh_from_db()
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.VALIDA)  # continua contando!
        self.assertTrue(saida.cancelamento_revisao_pendente)
        self.assertEqual(saida.cancelamento_origem, NotaFiscal.CancelamentoOrigem.POS_IMPORTACAO)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("70"))  # baixa continua aplicada
        pend = PendenciaMatching.objects.get(motivo="CANCELADA_SEFAZ")
        self.assertEqual(pend.nota_fiscal, saida)

    def test_verificar_cancelamentos_nao_verificada_nao_atualiza_nada(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        with mock.patch(
                "fiscal.sefaz_servico.sefaz.consultar_situacao_lote",
                return_value={saida.chave_acesso: ResultadoConsultaSefaz(SefazSituacao.NAO_VERIFICADA, erro="timeout")}):
            resumo = sefaz_servico.verificar_cancelamentos(chaves=[saida.chave_acesso])
        self.assertEqual(resumo.erros, 1)
        saida.refresh_from_db()
        self.assertIsNone(saida.situacao_sefaz_verificada_em)

    def test_resolver_pendencia_cancelada_sefaz_excluir_da_carteira(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        resultado_cancelada = ResultadoConsultaSefaz(SefazSituacao.CANCELADA, cstat="101", protocolo="777")
        with mock.patch("fiscal.sefaz_servico.sefaz.consultar_situacao_lote",
                        return_value={saida.chave_acesso: resultado_cancelada}):
            sefaz_servico.verificar_cancelamentos(chaves=[saida.chave_acesso])
        pend = PendenciaMatching.objects.get(motivo="CANCELADA_SEFAZ")

        self.client.force_login(get_user_model().objects.create_superuser("adm8", "a8@a.com", "x"))
        r = self.client.post(reverse("fiscal:resolver_pendencia", args=[pend.pk]), {"confirmar_cancelamento": "1"})
        self.assertEqual(r.status_code, 302)
        saida.refresh_from_db()
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.CANCELADA)
        self.assertFalse(saida.cancelamento_revisao_pendente)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("100"))
        pend.refresh_from_db()
        self.assertTrue(pend.resolvido)

    def test_resolver_pendencia_cancelada_sefaz_manter(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        resultado_cancelada = ResultadoConsultaSefaz(SefazSituacao.CANCELADA, cstat="101", protocolo="777")
        with mock.patch("fiscal.sefaz_servico.sefaz.consultar_situacao_lote",
                        return_value={saida.chave_acesso: resultado_cancelada}):
            sefaz_servico.verificar_cancelamentos(chaves=[saida.chave_acesso])
        pend = PendenciaMatching.objects.get(motivo="CANCELADA_SEFAZ")

        self.client.force_login(get_user_model().objects.create_superuser("adm10", "a10@a.com", "x"))
        r = self.client.post(
            reverse("fiscal:resolver_pendencia", args=[pend.pk]),
            {"ignorar_com_justificativa": "Cancelamento indevido, cliente confirmou que a operação é válida."})
        self.assertEqual(r.status_code, 302)
        saida.refresh_from_db()
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.VALIDA)
        self.assertFalse(saida.cancelamento_revisao_pendente)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("70"))  # baixa continua

    def test_botao_manual_verificar_cancelamentos(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        resultado_cancelada = ResultadoConsultaSefaz(SefazSituacao.CANCELADA, cstat="101", protocolo="777")
        self.client.force_login(get_user_model().objects.create_superuser("adm11", "a11@a.com", "x"))
        with mock.patch("fiscal.sefaz_servico.sefaz.consultar_situacao_lote",
                        return_value={saida.chave_acesso: resultado_cancelada}):
            r = self.client.post(reverse("fiscal:verificar_cancelamentos_sefaz"))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(PendenciaMatching.objects.filter(motivo="CANCELADA_SEFAZ").exists())

    def test_cron_endpoint_exige_token(self):
        r = self.client.get(reverse("fiscal:cron_verificar_cancelamentos"))
        self.assertEqual(r.status_code, 403)

    # ── excluir do controle de saldo — decisão manual, não é cancelamento
    # confirmado na SEFAZ (NotaFiscal.Situacao.EXCLUIDA, distinta de
    # CANCELADA) ──────────────────────────────────────────────────────────
    def test_excluir_item_do_saldo_some_do_saldo_e_sobrevive_ao_recalculo(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        item = self.item_entrada(100)
        matching.excluir_item_do_saldo(item)
        item.refresh_from_db()
        self.assertIsNone(item.saldo_atual)
        self.assertTrue(item.excluido_manualmente)
        matching.recalcular_baixas()
        item.refresh_from_db()
        self.assertIsNone(item.saldo_atual)  # não reatou no recálculo

    def test_excluir_nota_do_saldo_desfaz_baixas_e_nao_vira_cancelada(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        saida = self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        matching.excluir_nota_do_saldo(saida)
        saida.refresh_from_db()
        self.assertEqual(saida.situacao, NotaFiscal.Situacao.EXCLUIDA)
        self.assertEqual(self.item_entrada(100).saldo_atual, Decimal("100"))
        self.assertFalse(Vinculo.objects.exists())

    def test_telas_principais_renderizam_sem_erro(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        self.client.force_login(get_user_model().objects.create_superuser("adm12", "a12@a.com", "x"))
        for url, params in [
            (reverse("fiscal:index"), {}),
            (reverse("fiscal:historico"), {"modo": "entrada"}),
            (reverse("fiscal:historico"), {"modo": "entrada", "situacao": "CANCELADA"}),
            (reverse("fiscal:historico"), {"modo": "saida"}),
            (reverse("fiscal:historico"), {"modo": "saida", "insumos": "1"}),
            (reverse("fiscal:relatorios"), {}),
            (reverse("fiscal:relatorios"), {"situacao": "CANCELADA"}),
            (reverse("fiscal:saldo_tecidos"), {}),
        ]:
            r = self.client.get(url, params)
            self.assertEqual(r.status_code, 200, f"{url}?{params} -> {r.status_code}")

    def test_resolver_pendencia_get_renderiza_com_acao_de_exclusao(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "10"}]))
        self.importar(_entrada(101, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        pend = PendenciaMatching.objects.get(motivo="QUANTIDADE_EXCEDIDA")

        self.client.force_login(get_user_model().objects.create_superuser("adm3", "a3@a.com", "x"))
        r = self.client.get(reverse("fiscal:resolver_pendencia", args=[pend.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Excluir do saldo")

    @override_settings(FISCAL_MAX_ARQUIVOS_POR_ENVIO=500)
    def test_tela_de_importacao_renderiza_com_teto_de_arquivos(self):
        self.client.force_login(get_user_model().objects.create_superuser("adm4", "a4@a.com", "x"))
        r = self.client.get(reverse("fiscal:importar_entrada"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Permitido no máximo 500 arquivos")

    @override_settings(FISCAL_MAX_ARQUIVOS_POR_ENVIO=1)
    def test_upload_sem_js_recusa_sessao_acima_do_teto(self):
        self.client.force_login(get_user_model().objects.create_superuser("adm5", "a5@a.com", "x"))
        arquivos = [
            SimpleUploadedFile("a.xml", _entrada(100, [{**FLEECE, "q": "10"}]), content_type="text/xml"),
            SimpleUploadedFile("b.xml", _entrada(101, [{**FLEECE, "q": "10"}]), content_type="text/xml"),
        ]
        r = self.client.post(reverse("fiscal:importar_entrada"), {"arquivos": arquivos})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Permitido no máximo 1 arquivos")
        self.assertFalse(NotaFiscal.objects.exists())

    def test_upload_completo_sem_js_ate_confirmar(self):
        # Exercita o caminho real da view (não só importador.* direto): passo
        # 1 (upload sem JS grava tudo de uma vez) + passo 2 (confirmar sem
        # lote_inicio) — inclusive o prefetch_situacoes_sefaz em lote (ver
        # fiscal/views.py::_montar_previas/_confirmar_arquivos).
        self.client.force_login(get_user_model().objects.create_superuser("adm6", "a6@a.com", "x"))
        arquivo = SimpleUploadedFile("a.xml", _entrada(100, [{**FLEECE, "q": "10"}]), content_type="text/xml")
        r1 = self.client.post(reverse("fiscal:importar_entrada"), {"arquivos": [arquivo]})
        self.assertEqual(r1.status_code, 200)
        self.assertContains(r1, "Pronta pra importar")

        token = self.client.session["fiscal_import_token"]
        r2 = self.client.post(reverse("fiscal:confirmar_importacao_entrada"), {"token": token, "acao": "confirmar"})
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(self.item_entrada(100).q_com, Decimal("10"))

    def test_resolver_pendencia_excluir_item_do_saldo(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "10"}]))
        self.importar(_entrada(101, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        pend = PendenciaMatching.objects.get(motivo="QUANTIDADE_EXCEDIDA")

        self.client.force_login(get_user_model().objects.create_superuser("adm2", "a2@a.com", "x"))
        r = self.client.post(reverse("fiscal:resolver_pendencia", args=[pend.pk]),
                             {"excluir_entrada_item_id": self.item_entrada(100).pk})
        self.assertEqual(r.status_code, 302)
        item = self.item_entrada(100)
        self.assertIsNone(item.saldo_atual)
        self.assertTrue(item.excluido_manualmente)
        pend.refresh_from_db()
        self.assertTrue(pend.resolvido)

    # ── recalcular chega no mesmo lugar ─────────────────────────────────────
    def test_recalcular_baixas_e_idempotente(self):
        self.importar(_entrada(100, [{**FLEECE, "q": "100"}]))
        self.importar(_saida(1, [{**FLEECE, "q": "30", "ref": "100"}]))
        self.importar(_saida(3, [{**FLEECE, "q": "30", "ref": "100"}], dh="2026-09-08T10:00:00-03:00"))
        self.importar(_xml(2, emit=ZAN, dest=CLI, tp_nf="0", inf_cpl="ENTRADA REF NF 1",
                           itens=[{**FLEECE, "cfop": "1949", "q": "30"}]))
        antes = self.item_entrada(100).saldo_atual
        r = matching.recalcular_baixas()
        self.assertEqual(r.notas_anuladas, 1)
        self.assertEqual(self.item_entrada(100).saldo_atual, antes)
        self.assertEqual(antes, Decimal("70"))
