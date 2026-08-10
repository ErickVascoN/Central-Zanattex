"""Testes da checagem de completude do Corte.

Esta função é o portão do e-mail diário (`relatorios/cron.py`): o que ela
devolve vira o selo "Completo"/"Parcial" e a lista de pendências. Um erro
aqui é SILENCIOSO — o e-mail sai afirmando que o dia está completo enquanto
falta fonte, e ninguém percebe porque o e-mail chegou normalmente.
"""

from datetime import date
from unittest.mock import patch

import pandas as pd
from django.test import SimpleTestCase

from . import completude

DIA = date(2026, 8, 5)


def _df(*dias: date) -> pd.DataFrame:
    return pd.DataFrame({"DATA": pd.to_datetime([str(d) for d in dias])})


def _patches(arealva=None, iacanga=None, lencol=None, cortina=None):
    """Substitui os 4 carregadores. `None` = fonte sem dado nenhum."""
    vazio = pd.DataFrame({"DATA": pd.to_datetime([])})
    por_fonte = {"corte_arealva": arealva if arealva is not None else vazio,
                 "corte_iacanga": iacanga if iacanga is not None else vazio}
    return (
        patch.object(completude.servicos, "carregar_corte",
                     side_effect=lambda fonte: por_fonte[fonte]),
        patch.object(completude.lencol_servicos, "carregar_lencol",
                     return_value=lencol if lencol is not None else vazio),
        patch.object(completude.cortina_servicos, "carregar_cortina",
                     return_value=cortina if cortina is not None else vazio),
    )


class FontesIncompletasTests(SimpleTestCase):

    def _rodar(self, **kwargs) -> list[str]:
        p1, p2, p3 = _patches(**kwargs)
        with p1, p2, p3:
            return completude.fontes_incompletas(DIA)

    def test_todas_as_fontes_lancaram_nao_ha_pendencia(self):
        self.assertEqual(
            self._rodar(arealva=_df(DIA), iacanga=_df(DIA),
                        lencol=_df(DIA), cortina=_df(DIA)),
            [],
        )

    def test_fonte_sem_lancamento_no_dia_aparece_como_pendente(self):
        faltando = self._rodar(arealva=_df(DIA), iacanga=_df(DIA), lencol=_df(DIA))
        self.assertEqual(faltando, ["Cortina"])

    def test_nenhuma_fonte_lancou_lista_as_quatro(self):
        self.assertEqual(
            self._rodar(),
            ["Manta Arealva", "Manta Iacanga", "Lençol Arealva", "Cortina"],
        )

    def test_lancamento_em_outro_dia_nao_vale_pelo_dia_checado(self):
        # A fonte tem dados — só não do dia em questão. Se a checagem olhasse
        # "o df tem alguma linha?" em vez da data, o e-mail sairia "Completo"
        # todo dia a partir do primeiro lançamento da história.
        outro_dia = _df(date(2026, 8, 4))
        faltando = self._rodar(arealva=outro_dia, iacanga=_df(DIA),
                               lencol=_df(DIA), cortina=_df(DIA))
        self.assertEqual(faltando, ["Manta Arealva"])

    def test_itaju_nunca_entra_na_checagem(self):
        # Itaju pode legitimamente não ter corte num dia. Se alguém incluir
        # essa fonte na lista, o e-mail diário passa a sair "[Parcial]" pra
        # sempre e o selo perde o sentido.
        with patch.object(completude.servicos, "carregar_corte") as mock_carregar:
            mock_carregar.side_effect = lambda fonte: pd.DataFrame({"DATA": pd.to_datetime([])})
            with patch.object(completude.lencol_servicos, "carregar_lencol",
                              return_value=pd.DataFrame({"DATA": pd.to_datetime([])})), \
                 patch.object(completude.cortina_servicos, "carregar_cortina",
                              return_value=pd.DataFrame({"DATA": pd.to_datetime([])})):
                completude.fontes_incompletas(DIA)

        fontes_consultadas = [c.args[0] for c in mock_carregar.call_args_list]
        self.assertNotIn("corte_itaju", fontes_consultadas)
        self.assertEqual(sorted(fontes_consultadas), ["corte_arealva", "corte_iacanga"])

    def test_dataframe_vazio_conta_como_faltando_e_nao_quebra(self):
        # Fonte que ainda não sincronizou devolve df vazio — tem que virar
        # pendência, não exceção (exceção derruba o envio inteiro).
        self.assertIn("Manta Arealva", self._rodar(iacanga=_df(DIA),
                                                     lencol=_df(DIA), cortina=_df(DIA)))
