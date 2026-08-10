"""Teste da base do comparativo entre períodos (só dias úteis).

Regressão de um alarme falso real, medido em 08/08/2026: a janela de agosto
pegou o sábado 01/08, que rendeu 1.780 peças contra uma média de 36.749 num
dia útil (~5% de um dia normal). Com o sábado na conta, o selo de "Média
diária" acusava -40,2%; entre dias úteis a queda real era -21,6%.

Quase o dobro. Um número desses faz alguém correr atrás de um problema que
não existe — e, depois de algumas vezes, faz parar de olhar o painel.
Sábado é estruturalmente diferente aqui (o módulo de premiação já o trata à
parte pelo mesmo motivo), então o comparativo ignora fim de semana dos dois
lados da conta.
"""

import pandas as pd
from django.test import SimpleTestCase

from .views import _dias_uteis

# 01/08/2026 é sábado; 02/08 domingo; 03–07/08 seg–sex.
SABADO = "2026-08-01"
DOMINGO = "2026-08-02"
SEGUNDA = "2026-08-03"
SEXTA = "2026-08-07"


def _df(*datas: str) -> pd.DataFrame:
    return pd.DataFrame({
        "DATA": pd.to_datetime(list(datas)),
        "QUANTIDADE": [100] * len(datas),
    })


class DiasUteisTests(SimpleTestCase):

    def test_sabado_fica_de_fora(self):
        out = _dias_uteis(_df(SABADO, SEGUNDA))
        self.assertEqual(list(out["DATA"].dt.strftime("%Y-%m-%d")), [SEGUNDA])

    def test_domingo_fica_de_fora(self):
        out = _dias_uteis(_df(DOMINGO, SEGUNDA))
        self.assertEqual(len(out), 1)

    def test_segunda_a_sexta_passam_inteiras(self):
        semana = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
        self.assertEqual(len(_dias_uteis(_df(*semana))), 5)

    def test_sabado_de_baixa_producao_nao_derruba_a_media(self):
        # O caso real: 3 dias úteis fortes + 1 sábado fraco. Com o sábado a
        # média cai ~25%; sem ele, a média reflete o ritmo de produção.
        df = pd.DataFrame({
            "DATA": pd.to_datetime([SEGUNDA, "2026-08-04", "2026-08-05", SABADO]),
            "QUANTIDADE": [36000, 36000, 36000, 1780],
        })
        media_com_sabado = df["QUANTIDADE"].sum() / 4
        uteis = _dias_uteis(df)
        media_sem_sabado = uteis["QUANTIDADE"].sum() / len(uteis)

        self.assertEqual(len(uteis), 3)
        self.assertEqual(media_sem_sabado, 36000)
        self.assertLess(media_com_sabado, 28000)   # o número enganoso
        self.assertGreater(media_sem_sabado - media_com_sabado, 8000)

    def test_semana_sem_dia_util_fica_vazia_sem_quebrar(self):
        # Janela só com fim de semana: o resultado é vazio, e `variacao()`
        # devolve None em cima disso (base zero não vira "+100%").
        self.assertTrue(_dias_uteis(_df(SABADO, DOMINGO)).empty)
