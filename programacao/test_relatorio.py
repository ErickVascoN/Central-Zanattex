"""
Invariantes da tabela do relatório de Programação de Corte.

Os dois bugs que motivaram estes testes:
  - OPs com vários itens cujo corte o cruzamento não conseguiu ratear carregam
    o TOTAL da OP repetido em cada linha; somar linha a linha dava o dobro do
    programado real (193.143 onde o card dizia 86.649);
  - o programado tem que fechar com a coluna QNT. PROG da planilha, que é o
    número que dá para conferir na origem.
"""

from django.test import SimpleTestCase
import pandas as pd

from . import servicos


def _linha(op, semana, item, prog, prog_op, cortada, cortada_op, status, status_op):
    return {
        "_CHAVE": op, "PED. CLIENTE": op, "OP_RESOLVIDA": op, "SEMANA": semana,
        "CLIENTE": "CAMESA", "LOCAL": "CORTE LENÇOL", "CATEGORIA": "Jogo de cama",
        "PRODUTO": "JOGO DE CAMA", "DESCRIÇÃO DO PRODUTO": item,
        "QNT. PROG": prog, "QNT_PROG_TOTAL": prog, "QNT_PROG_OP": prog_op,
        "QNT_CORTADA": cortada, "QNT_CORTADA_OP": cortada_op,
        "STATUS_CORTE": status, "STATUS_CORTE_OP": status_op,
    }


class LinhasProgramacaoTests(SimpleTestCase):
    def _df(self):
        # OP 1: 4 itens cortados sem rateio por produto — cada linha carrega o
        # total cortado da OP (30.000) repetido, que é o caso que duplicava.
        sem_rateio = [
            _linha("PROG 85", "SEMANA 36", f"ITEM {i}", prog, 35498, 30000, 30000,
                   "Parcial", "Parcial")
            for i, prog in enumerate([15000, 1500, 1998, 17000], start=1)
        ]
        # OP 2: 2 itens com corte rateado por produto
        com_rateio = [
            _linha("61473", "SEMANA 36", "ITEM A", 1000, 1500, 600, 900,
                   "Parcial", "Parcial"),
            _linha("61473", "SEMANA 36", "ITEM B", 500, 1500, 300, 900,
                   "Parcial", "Parcial"),
        ]
        # OP 3: item único, concluído
        unica = [_linha("267352", "SEMANA 36", "FRONHA", 200, 200, 200, 200,
                        "Concluído", "Concluído")]
        return pd.DataFrame(sem_rateio + com_rateio + unica)

    def test_programado_fecha_com_a_planilha(self):
        df = self.df = self._df()
        r = servicos.linhas_programacao(df)
        self.assertEqual(r["totais"]["prog"], int(df["QNT. PROG"].sum()))

    def test_op_sem_rateio_vira_uma_linha_so(self):
        r = servicos.linhas_programacao(self._df())
        prog85 = [l for l in r["linhas"] if l["op"] == "PROG 85"]
        self.assertEqual(len(prog85), 1, "OP sem rateio não pode repetir o total")
        self.assertEqual(prog85[0]["prog"], 35498)      # soma dos 4 itens
        self.assertEqual(prog85[0]["cortado"], 30000)   # o total da OP, uma vez
        self.assertEqual(prog85[0]["itens"], 4)

    def test_op_sem_corte_sai_item_a_item(self):
        """Sem corte não há o que ratear: os itens aparecem separados (mais
        informação) e a soma continua fechando."""
        df = self._df()
        df.loc[df["_CHAVE"] == "PROG 85", ["QNT_CORTADA", "QNT_CORTADA_OP"]] = 0
        r = servicos.linhas_programacao(df)
        prog85 = [l for l in r["linhas"] if l["op"] == "PROG 85"]
        self.assertEqual(len(prog85), 4)
        self.assertEqual(sum(l["prog"] for l in prog85), 35498)

    def test_op_rateada_sai_item_a_item(self):
        r = servicos.linhas_programacao(self._df())
        op = [l for l in r["linhas"] if l["op"] == "61473"]
        self.assertEqual(len(op), 2)
        self.assertEqual(sum(l["prog"] for l in op), 1500)
        self.assertEqual(sum(l["cortado"] for l in op), 900)

    def test_contagem_de_ops_por_status(self):
        r = servicos.linhas_programacao(self._df())
        self.assertEqual(r["ops"]["total"], 3)          # 3 OPs, não 7 linhas
        self.assertEqual(r["ops"]["Concluído"], 1)
        self.assertEqual(r["ops"]["Parcial"], 2)
        self.assertEqual(r["ops"]["Pendente"], 0)

    def test_totais_dos_blocos_somam_o_total_geral(self):
        r = servicos.linhas_programacao(self._df())
        self.assertEqual(sum(b["totais"]["prog"] for b in r["blocos"]),
                         r["totais"]["prog"])
        self.assertEqual(sum(b["totais"]["cortado"] for b in r["blocos"]),
                         r["totais"]["cortado"])

    def test_marca_op_cortada_em_outra_semana(self):
        from datetime import date
        r = servicos.linhas_programacao(
            self._df(),
            datas_corte={
                "61473": {"datas": [date(2026, 8, 11), date(2026, 8, 12)],
                          "semanas": {"33"}},
                "267352": {"datas": [date(2026, 9, 2)], "semanas": {"36"}},
            },
            semanas_filtro=["SEMANA 36"], mostrar_quando=True)
        op = [l for l in r["linhas"] if l["op"] == "61473"]
        self.assertEqual(op[0]["semanas_em"], "33")
        self.assertEqual(op[0]["datas_em"], "11/08, 12/08")
        self.assertTrue(op[0]["corte_fora"], "cortada na 33, filtro é a 36")
        # só a 1ª linha da OP carrega os valores: o corte é lançado por OP
        self.assertIsNone(op[1]["semanas_em"])
        self.assertIsNone(op[1]["datas_em"])
        na_semana = [l for l in r["linhas"] if l["op"] == "267352"][0]
        self.assertEqual(na_semana["semanas_em"], "36")
        self.assertEqual(na_semana["datas_em"], "02/09")
        self.assertFalse(na_semana["corte_fora"])

    def test_texto_datas_resume_quando_sao_muitas(self):
        from datetime import date
        poucas = [date(2026, 9, 1), date(2026, 9, 3)]
        self.assertEqual(servicos.texto_datas(poucas), "01/09, 03/09")
        muitas = [date(2026, 9, d) for d in range(1, 8)]
        self.assertEqual(servicos.texto_datas(muitas), "01/09 a 07/09 (7 dias)")
        self.assertEqual(servicos.texto_datas([]), "—")

    def test_marca_programado_dobrado_so_quando_a_op_esta_parada(self):
        """A OP 'PROG 85' tem 35.498 programados para 30.000 cortados (1,18) —
        não é dobro. Forçando o cortado para metade do programado, ela vira
        suspeita; mas só se o corte for antigo, senão pode ser faseamento."""
        from datetime import date
        df = self._df()
        df.loc[df["_CHAVE"] == "PROG 85", ["QNT_CORTADA", "QNT_CORTADA_OP"]] = 17749
        datas = {"PROG 85": {"datas": [date(2026, 8, 11)], "semanas": {"33"}}}

        parada = servicos.linhas_programacao(
            df, datas_corte=datas, data_base=date(2026, 9, 5))
        self.assertEqual([s["op"] for s in parada["suspeitas_dobro"]], ["PROG 85"])
        self.assertEqual(parada["suspeitas_dobro"][0]["prog"], 35498)
        self.assertEqual(parada["suspeitas_dobro"][0]["cortado"], 17749)

        # mesmo dobro, mas cortada anteontem: ainda pode estar em andamento
        recente = servicos.linhas_programacao(
            df, datas_corte=datas, data_base=date(2026, 8, 13))
        self.assertEqual(recente["suspeitas_dobro"], [])

    def test_nao_marca_op_com_execucao_normal(self):
        from datetime import date
        r = servicos.linhas_programacao(
            self._df(),
            datas_corte={"267352": {"datas": [date(2026, 8, 1)], "semanas": {"31"}}},
            data_base=date(2026, 9, 5))
        # 267352 cortou 200 de 200 programados — razão 1, não entra
        self.assertNotIn("267352", [s["op"] for s in r["suspeitas_dobro"]])

    def test_limite_nao_altera_os_totais(self):
        df = self._df()
        cheio = servicos.linhas_programacao(df)
        capado = servicos.linhas_programacao(df, limite=1)
        self.assertEqual(capado["totais"], cheio["totais"])
        self.assertTrue(capado["truncado"])
        self.assertEqual(sum(b["ocultas"] for b in capado["blocos"]),
                         cheio["total"] - 1)
