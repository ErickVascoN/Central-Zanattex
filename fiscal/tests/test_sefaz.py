"""Parsing da resposta da SEFAZ (fiscal/sefaz.py::_interpretar_resposta) —
XMLs sintéticos, no formato real que retConsSitNFe devolve. Não bate na
rede (testa só a função de parsing, com um lxml.etree.Element montado à
mão — é exatamente o formato que `_interpretar_resposta` recebe depois que
o zeep desembrulha o xsd:any).

O teste de nota cancelada replica um bug real achado em 2026-09-23 testando
contra uma NF de verdade cancelada em produção: o código lia o cStat de
dentro de protNFe (que nunca muda, sempre autorizada) em vez do cStat do
topo do retConsSitNFe (a situação atual) — uma nota cancelada voltava como
autorizada. Ver memory/sefaz-cancelamento-plano.md."""
from __future__ import annotations

import time
from datetime import datetime
from unittest import mock

from django.test import SimpleTestCase
from lxml import etree

from fiscal import sefaz
from fiscal.sefaz import Situacao, _interpretar_resposta

NS = "http://www.portalfiscal.inf.br/nfe"
CHAVE = "35260714601572000130550020000392491810049990"


def _no(xml: str) -> etree._Element:
    return etree.fromstring(xml.encode())


class InterpretarRespostaTests(SimpleTestCase):
    def test_nota_autorizada(self):
        no = _no(f"""
        <retConsSitNFe xmlns="{NS}" versao="4.00">
            <cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
            <chNFe>{CHAVE}</chNFe>
            <protNFe versao="4.00"><infProt>
                <chNFe>{CHAVE}</chNFe><dhRecbto>2026-07-01T15:42:19-03:00</dhRecbto>
                <nProt>135262598822130</nProt><cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
            </infProt></protNFe>
        </retConsSitNFe>""")
        r = _interpretar_resposta(no)
        self.assertEqual(r.situacao, Situacao.AUTORIZADA)
        self.assertEqual(r.protocolo, "135262598822130")
        self.assertEqual(r.dh_recbto, datetime.fromisoformat("2026-07-01T15:42:19-03:00"))

    def test_nota_cancelada_por_evento_nao_confunde_com_protnfe_original(self):
        # Reproduz a resposta real da SEFAZ pra uma NF cancelada: cStat=101
        # no TOPO, mas protNFe (imutável) continua mostrando cStat=100 —
        # é exatamente o padrão que causou o bug original.
        no = _no(f"""
        <retConsSitNFe xmlns="{NS}" versao="4.00">
            <cStat>101</cStat><xMotivo>Cancelamento de NF-e homologado</xMotivo>
            <chNFe>{CHAVE}</chNFe>
            <protNFe versao="4.00"><infProt>
                <chNFe>{CHAVE}</chNFe><dhRecbto>2026-07-01T15:42:19-03:00</dhRecbto>
                <nProt>135262598822130</nProt><cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
            </infProt></protNFe>
            <retCancNFe versao="3.10"><infCanc>
                <chNFe>{CHAVE}</chNFe><dhRecbto>2026-07-01T15:42:19-03:00</dhRecbto>
                <nProt>135262598822130</nProt><cStat>101</cStat><xMotivo>Cancelamento de NF-e homologado</xMotivo>
            </infCanc></retCancNFe>
        </retConsSitNFe>""")
        r = _interpretar_resposta(no)
        self.assertEqual(r.situacao, Situacao.CANCELADA)
        self.assertTrue(r.cancelada)
        self.assertEqual(r.cstat, "101")

    def test_nota_cancelada_por_evento_moderno_sem_retcancnfe(self):
        # Modelo pós-2012 sem o bloco retCancNFe (algumas UFs/versões não
        # ecoam mais esse bloco legado) — protocolo vem só do evento 110111.
        no = _no(f"""
        <retConsSitNFe xmlns="{NS}" versao="4.00">
            <cStat>101</cStat><xMotivo>Cancelamento de NF-e homologado</xMotivo>
            <chNFe>{CHAVE}</chNFe>
            <protNFe versao="4.00"><infProt>
                <chNFe>{CHAVE}</chNFe><dhRecbto>2026-07-01T15:42:19-03:00</dhRecbto>
                <nProt>135262598822130</nProt><cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo>
            </infProt></protNFe>
            <procEventoNFe versao="1.00">
                <retEvento versao="1.00"><infEvento>
                    <cStat>135</cStat><xMotivo>Evento registrado e vinculado a NF-e</xMotivo>
                    <chNFe>{CHAVE}</chNFe><tpEvento>110111</tpEvento>
                    <nProt>135262599115192</nProt><dhRegEvento>2026-07-01T16:00:46-03:00</dhRegEvento>
                </infEvento></retEvento>
            </procEventoNFe>
        </retConsSitNFe>""")
        r = _interpretar_resposta(no)
        self.assertEqual(r.situacao, Situacao.CANCELADA)
        self.assertEqual(r.protocolo, "135262599115192")

    def test_chave_nao_encontrada_nao_vira_autorizada(self):
        no = _no(f"""
        <retConsSitNFe xmlns="{NS}" versao="4.00">
            <cStat>217</cStat><xMotivo>Rejeição: NF-e não consta na base de dados da SEFAZ</xMotivo>
            <chNFe>{CHAVE}</chNFe>
        </retConsSitNFe>""")
        r = _interpretar_resposta(no)
        self.assertEqual(r.situacao, Situacao.NAO_VERIFICADA)
        self.assertFalse(r.cancelada)

    def test_chave_rejeitada_nao_vira_autorizada(self):
        no = _no(f"""
        <retConsSitNFe xmlns="{NS}" versao="4.00">
            <cStat>236</cStat><xMotivo>Rejeição: Chave de Acesso com dígito verificador inválido</xMotivo>
        </retConsSitNFe>""")
        r = _interpretar_resposta(no)
        self.assertEqual(r.situacao, Situacao.NAO_VERIFICADA)

    def test_resposta_vazia(self):
        r = _interpretar_resposta(None)
        self.assertEqual(r.situacao, Situacao.NAO_VERIFICADA)


class ConsumoIndevidoTests(SimpleTestCase):
    """cStat 656 (consumo indevido): depois do primeiro, o CNPJ não é mais
    consultado até o bloqueio passar — insistir só prolonga o bloqueio
    (visto em produção em 2026-10-01, importando 2.769 notas de uma vez)."""

    CNPJ = "14601572000130"

    def setUp(self):
        sefaz._bloqueado_ate.clear()
        self.addCleanup(sefaz._bloqueado_ate.clear)

    def _resposta_656(self):
        return _no(f"""<retConsSitNFe xmlns="{NS}" versao="4.00">
            <cStat>656</cStat><xMotivo>Rejeição: Consumo Indevido</xMotivo></retConsSitNFe>""")

    def test_primeiro_656_bloqueia_o_cnpj_e_as_proximas_nao_vao_a_rede(self):
        chamadas = []

        def fake_cliente(*args, **kwargs):
            cliente = mock.Mock()
            cliente.service.nfeConsultaNF = lambda xml: chamadas.append(1) or self._resposta_656()
            return cliente

        with mock.patch.object(sefaz, "montar_sessao_soap", return_value=mock.Mock()), \
                mock.patch.object(sefaz, "_cliente_soap", side_effect=fake_cliente):
            r1 = sefaz.consultar_situacao(CHAVE, self.CNPJ, uf="SP")
            r2 = sefaz.consultar_situacao(CHAVE, self.CNPJ, uf="SP")

        self.assertEqual(r1.situacao, Situacao.NAO_VERIFICADA)
        self.assertEqual(r1.cstat, "656")
        self.assertEqual(r2.situacao, Situacao.NAO_VERIFICADA)
        self.assertIn("consumo indevido", r2.erro)
        self.assertEqual(len(chamadas), 1)  # a segunda nem chegou na SEFAZ

    def test_bloqueio_de_um_cnpj_nao_afeta_outro(self):
        sefaz._bloqueado_ate[self.CNPJ] = time.monotonic() + 600
        self.assertGreater(sefaz._bloqueio_restante(self.CNPJ), 0)
        self.assertEqual(sefaz._bloqueio_restante("38320245000179"), 0)

    def test_lote_resolve_uf_uma_vez_por_cnpj_fora_das_threads(self):
        pares = [(CHAVE, self.CNPJ)] * 5
        with mock.patch.object(sefaz, "_uf_do_cnpj", return_value="SP") as uf_do_cnpj, \
                mock.patch.object(sefaz, "consultar_situacao",
                                  return_value=sefaz.ResultadoConsultaSefaz(Situacao.AUTORIZADA)) as consulta:
            sefaz.consultar_situacao_lote(pares)
        self.assertEqual(uf_do_cnpj.call_count, 1)
        self.assertTrue(all(c.args[2] == "SP" for c in consulta.call_args_list))
