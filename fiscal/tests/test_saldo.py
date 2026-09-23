"""Regras do Saldo Fiscal que a auditoria de set/2026 achou quebradas — cada
teste é um caso real que distorcia o saldo apresentado ao cliente (nota
anulada por estorno baixando, nota sem autorização baixando, item zerado
virando "sem correspondente", baixa dupla na resolução manual, KG somado com
MT...). XMLs sintéticos, mínimos, no formato da NF-e."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from fiscal import importador, matching, servicos
from fiscal.models import Cliente, NotaFiscal, NotaFiscalItem, PendenciaMatching, Vinculo

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


@override_settings(FISCAL_CNPJS_ZANATTEX={ZAN},
                   FISCAL_NCMS_CONTROLADOS={"60019200", "54075210", "54075400"})
class SaldoFiscalTests(TestCase):
    def setUp(self):
        Cliente.objects.create(nome="Camesa", cnpj=CLI)

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
