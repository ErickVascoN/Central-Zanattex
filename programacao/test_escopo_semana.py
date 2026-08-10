"""Testes da regra de escopo do filtro de Semana (Programação de Corte).

Esta é a regra mais contra-intuitiva do módulo, e por isso a mais arriscada:
com o filtro de Semana ativo, `QNT_CORTADA` (e portanto Status e Eficiência)
NÃO se restringe à semana — continua somando todo o corte já feito da OP.

Isso parece bug e não é. Se restringisse, uma OP programada para a semana 30
e cortada com atraso na semana 32 apareceria como "Pendente" ao filtrar a
semana 30, quando na verdade ela foi CONCLUÍDA (só que atrasada). O Status
responde "esta OP está pronta?" — e essa resposta não pode mudar conforme o
filtro.

A pergunta "quanto saiu nesta semana?" é respondida por outro número,
`qnt_cortada_por_semana()`, que convive com o total histórico em vez de
substituí-lo. Estes testes existem para que ninguém "conserte" a primeira
regra achando que é bug — os dois números têm que continuar existindo.
"""

import pandas as pd
from django.test import SimpleTestCase

from . import servicos


def _cortes(linhas: list[tuple[str, int, int]]) -> pd.DataFrame:
    """(OP, SEMANA ISO do corte real, QUANTIDADE)."""
    return pd.DataFrame({
        "OP": [op for op, _, _ in linhas],
        "SEMANA": [sem for _, sem, _ in linhas],
        "QUANTIDADE": [qtd for _, _, qtd in linhas],
    })


class QntCortadaPorSemanaTests(SimpleTestCase):

    def test_soma_apenas_as_semanas_selecionadas(self):
        df = _cortes([("1001", 30, 500), ("1001", 32, 300)])
        self.assertEqual(servicos.qnt_cortada_por_semana(df, [30]), {"1001": 500})

    def test_op_cortada_com_atraso_nao_aparece_na_semana_programada(self):
        # O corte inteiro saiu na semana 32. Filtrando a 30, "cortado na
        # semana" é ZERO — é exatamente essa a informação que o total
        # histórico (QNT_CORTADA) esconderia sozinho.
        df = _cortes([("1001", 32, 800)])
        self.assertEqual(servicos.qnt_cortada_por_semana(df, [30]), {})

    def test_varias_semanas_selecionadas_somam_juntas(self):
        df = _cortes([("1001", 30, 500), ("1001", 32, 300), ("1001", 35, 100)])
        self.assertEqual(servicos.qnt_cortada_por_semana(df, [30, 32]), {"1001": 800})

    def test_sem_filtro_de_semana_nao_produz_coluna(self):
        # Sem semana selecionada a coluna extra não deve nem aparecer na tela
        # (o dict vazio é o sinal disso pra `resumo_tabela`).
        df = _cortes([("1001", 30, 500)])
        self.assertEqual(servicos.qnt_cortada_por_semana(df, []), {})

    def test_ops_diferentes_nao_se_misturam(self):
        df = _cortes([("1001", 30, 500), ("2002", 30, 700)])
        self.assertEqual(servicos.qnt_cortada_por_semana(df, [30]),
                         {"1001": 500, "2002": 700})

    def test_op_em_branco_e_descartada(self):
        df = _cortes([("", 30, 999), ("1001", 30, 500)])
        self.assertEqual(servicos.qnt_cortada_por_semana(df, [30]), {"1001": 500})

    def test_op_casa_mesmo_escrita_de_formas_diferentes(self):
        # A planilha de corte e a de programação escrevem OP de jeitos
        # diferentes ("OP 1001", "1001.0"); o cruzamento é por chave
        # normalizada, senão o cortado-na-semana daria zero à toa.
        df = _cortes([("OP 1001", 30, 500), ("1001.0", 30, 300)])
        self.assertEqual(servicos.qnt_cortada_por_semana(df, [30]), {"1001": 800})


class ResumoTabelaEscopoTests(SimpleTestCase):
    """A tabela mostra os DOIS números lado a lado — é o contrato que
    garante que um não virou substituto do outro."""

    def _df_agg(self, qnt_cortada_historico: int) -> pd.DataFrame:
        return pd.DataFrame([{
            "SEMANA": 30, "PED. CLIENTE": "1001", "CLIENTE": "ACME",
            "LOCAL": "AREALVA", "PRODUTO": "MANTA", "QNT_PROG_TOTAL": 1000,
            "QNT_CORTADA": qnt_cortada_historico, "DIFERENÇA": 0,
            "EFICIÊNCIA_PRC": 100.0, "STATUS_PROD": "Liberado",
            "STATUS_CORTE": "Concluído", "OP_RESOLVIDA": "1001",
        }])

    def test_total_historico_e_cortado_na_semana_convivem(self):
        # OP concluída (1000 peças no histórico), mas só 200 saíram na semana
        # filtrada. A linha tem que carregar os dois números — e o status
        # continua "Concluído", não vira "Parcial" por causa do filtro.
        linhas = servicos.resumo_tabela(self._df_agg(1000), {"1001": 200})
        linha = linhas[0]
        self.assertEqual(linha["qnt_cortada"], 1000)      # histórico da OP
        self.assertEqual(linha["cortado_semana"], 200)    # só a semana filtrada
        self.assertEqual(linha["status_corte"], "Concluído")

    def test_sem_filtro_a_coluna_da_semana_nem_existe(self):
        linhas = servicos.resumo_tabela(self._df_agg(1000), None)
        self.assertNotIn("cortado_semana", linhas[0])
        self.assertEqual(linhas[0]["qnt_cortada"], 1000)

    def test_op_sem_corte_na_semana_mostra_zero_sem_sumir_da_tabela(self):
        # Cortada com atraso fora da semana filtrada: aparece com 0 na coluna
        # da semana, mas continua na tabela e continua "Concluído".
        linhas = servicos.resumo_tabela(self._df_agg(1000), {"9999": 500})
        self.assertEqual(linhas[0]["cortado_semana"], 0)
        self.assertEqual(linhas[0]["status_corte"], "Concluído")
