"""Regressão: a aba "LITEX (ENFARDAMENTO)" sumia da lista de facções.

Detectado porque LITEX não aparecia no dropdown "quem irá produzir" da
Programação de Corte (só "COSTURA INTERNA" e as demais facções). A aba
mudou de formato — virou um log linha-a-linha por colaborador/etapa de
processo (SETOR/PROCESSO), não mais um total agregado por empresa/produto —
e o `col_map` antigo ("EMPRESA"/"PECAS") não batia com nenhuma coluna real
("CLIENTE"/"TOTAL CONFERIDO"), então `_load_tab` descartava a aba inteira em
silêncio (`colunas essenciais não encontradas`).

Dois efeitos colaterais do formato novo, cobertos aqui: a coluna DATA vem
como serial numérico do Excel/Sheets (sem separador, o parser de string
comum retorna NaT pra tudo) e cada peça passa por várias linhas/etapas
(ELASTICADO, BAINHA, ... EMBALADO/EMBALADA) — sem filtrar só a etapa final
de embalagem, a soma conta a mesma peça várias vezes.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from integracao.fontes import FACCOES_ABAS
from producao.faccao_loader import _load_tab

_CFG = FACCOES_ABAS["LITEX (ENFARDAMENTO)"]

_CSV = (
    "COD OBS,LITEX  PRESTADOR,DATA,SETOR,PRODUTO,PROCESSO,DESCRIÇÃO,CLIENTE,TOTAL CONFERIDO,OBSERVAÇÕES\n"
    ",VANESSA,46079,COSTURA,,ELASTICADO,,NIAZI,,\n"           # etapa intermediária, sem produto
    ",MARIAH,46140,MESA,LENÇOL CS,EMBALADO,HAVAN,NIAZI,828,\n"
    ",VIVIANE,46141,MESA,LENÇOL ST,EMBALADA,PACO MILANO,SULTAN,45,\n"  # variação de gênero
)


class LitexEnfardamentoTests(SimpleTestCase):

    def setUp(self):
        patcher = patch("producao.faccao_loader.get_raw", return_value=_CSV)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_so_etapa_final_de_embalagem_entra(self):
        df = _load_tab("sheet", "LITEX (ENFARDAMENTO)", _CFG, ttl=120)
        self.assertEqual(len(df), 2)
        self.assertTrue((df["FACCAO"] == "LITEX").all())
        self.assertEqual(sorted(df["QUANTIDADE"].tolist()), [45, 828])

    def test_data_serial_excel_vira_data_real(self):
        df = _load_tab("sheet", "LITEX (ENFARDAMENTO)", _CFG, ttl=120)
        datas = sorted(df["DATA"].dt.strftime("%Y-%m-%d").tolist())
        self.assertEqual(datas, ["2026-04-28", "2026-04-29"])
