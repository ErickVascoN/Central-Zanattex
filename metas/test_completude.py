"""Testes da checagem de completude da Produção Diária (facções externas).

Mesmo papel do `corte/completude.py`: alimenta o selo "Completo"/"Parcial"
e a lista de pendências do e-mail diário (`relatorios/cron.py`). As regras
aqui são de ESCOPO — quem entra e quem não entra na cobrança — e errar o
escopo tem dois modos de falha, ambos ruins:
  · escopo largo demais → e-mail eternamente "[Parcial]", selo perde sentido;
  · escopo estreito demais → e-mail diz "Completo" com facção faltando.
"""

from datetime import date
from unittest.mock import patch

import pandas as pd
from django.test import SimpleTestCase

from . import completude
from .matching import LITTEX_SENTINEL

DIA = date(2026, 8, 5)
MES = date(2026, 8, 1)


def _plano(*responsaveis: str) -> dict:
    return {"acabado": pd.DataFrame({"RESPONSAVEL_RESOLVIDO": list(responsaveis)})}


def _producao(linhas: list[tuple[str, date]]) -> pd.DataFrame:
    return pd.DataFrame({
        "FACCAO": [f for f, _ in linhas],
        "DATA": pd.to_datetime([str(d) for _, d in linhas]),
    })


def _producao_obs(linhas: list[tuple[str, date, int, str]]) -> pd.DataFrame:
    """Produção com QUANTIDADE e OBSERVACAO — pros casos de linha zerada."""
    return pd.DataFrame({
        "FACCAO": [f for f, _, _, _ in linhas],
        "DATA": pd.to_datetime([str(d) for _, d, _, _ in linhas]),
        "QUANTIDADE": [q for _, _, q, _ in linhas],
        "OBSERVACAO": [o for _, _, _, o in linhas],
    })


class PrestadoresFaltandoTests(SimpleTestCase):

    def _rodar(self, plano, producao, *, plano_bruto_vazio=False, meses=(MES,)):
        bruto = pd.DataFrame() if plano_bruto_vazio else pd.DataFrame({"x": [1]})
        with patch.object(completude.servicos, "carregar_plano_metas_bruto",
                          return_value=bruto), \
             patch.object(completude.servicos, "meses_disponiveis",
                          return_value=list(meses)), \
             patch.object(completude.servicos, "cruzar_mes", return_value=plano), \
             patch.object(completude, "carregar_producao", return_value=producao):
            return completude.prestadores_faltando(DIA)

    def test_faccao_esperada_que_nao_lancou_vira_pendencia(self):
        # BETA é facção conhecida (lançou dia 04), mas não lançou no dia 05.
        faltando = self._rodar(
            _plano("ALFA", "BETA"),
            _producao([("ALFA", DIA), ("BETA", date(2026, 8, 4))]),
        )
        self.assertEqual(faltando, ["BETA"])

    def test_todas_lancaram_nao_ha_pendencia(self):
        faltando = self._rodar(
            _plano("ALFA", "BETA"),
            _producao([("ALFA", DIA), ("BETA", DIA)]),
        )
        self.assertEqual(faltando, [])

    def test_lancamento_em_outro_dia_nao_conta(self):
        faltando = self._rodar(
            _plano("ALFA"),
            _producao([("ALFA", date(2026, 8, 4))]),
        )
        self.assertEqual(faltando, ["ALFA"])

    def test_littex_interna_nunca_e_cobrada(self):
        # LITTEX é unidade interna — esta checagem cobre só facções externas.
        # Se entrasse, o e-mail sairia "[Parcial]" todo santo dia.
        faltando = self._rodar(
            _plano(LITTEX_SENTINEL, "ALFA"),
            _producao([("ALFA", DIA)]),
        )
        self.assertEqual(faltando, [])

    def test_responsavel_desconhecido_na_producao_nao_e_cobrado(self):
        # Nome que está no Plano de Metas mas nunca apareceu na base de
        # produção (prestador interno, empresa que mudou de nome etc.) não
        # pode virar pendência eterna de algo que nunca vai ser lançado ali.
        # "Conhecida" = aparece em QUALQUER data da produção, não no dia.
        faltando = self._rodar(
            _plano("ALFA", "FANTASMA LTDA"),
            _producao([("ALFA", DIA)]),
        )
        self.assertEqual(faltando, [])

    def test_sem_plano_cadastrado_no_mes_nao_bloqueia_o_envio(self):
        # Nada a checar ≠ tudo faltando. Sem plano do mês, o e-mail deve sair
        # normalmente em vez de acusar pendência inventada.
        faltando = self._rodar(_plano("ALFA"), _producao([]), meses=[date(2026, 7, 1)])
        self.assertEqual(faltando, [])

    def test_plano_bruto_vazio_nao_bloqueia_o_envio(self):
        faltando = self._rodar(_plano("ALFA"), _producao([]), plano_bruto_vazio=True)
        self.assertEqual(faltando, [])

    def test_plano_do_mes_sem_acabado_nao_bloqueia_o_envio(self):
        faltando = self._rodar(
            {"acabado": pd.DataFrame({"RESPONSAVEL_RESOLVIDO": []})},
            _producao([]),
        )
        self.assertEqual(faltando, [])

    # ---- zero confirmado × zero provisório -------------------------------
    # Os dois chegam na planilha como QUANTIDADE=0 + observação livre, mas
    # dizem coisas opostas: um é o dado ("não produziu"), o outro é a ausência
    # dele ("ainda não me enviou"). Só o primeiro encerra a cobrança.

    def test_zero_com_observacao_de_motivo_conta_como_lancado(self):
        # "Não produziu porque a máquina quebrou" É a informação do dia —
        # cobrar de novo seria pedir algo que já foi respondido.
        faltando = self._rodar(
            _plano("ALFA", "BETA"),
            _producao_obs([
                ("ALFA", DIA, 500, ""),
                ("BETA", DIA, 0, "Maquina quebrou, sem producao"),
            ]),
        )
        self.assertEqual(faltando, [])

    def test_zero_aguardando_envio_continua_pendente(self):
        # A linha existe só pra registrar que o número não chegou. Antes,
        # qualquer linha no dia tirava a facção da cobrança — o e-mail
        # silenciava justamente quem ainda devia o dado.
        faltando = self._rodar(
            _plano("ALFA", "BETA"),
            _producao_obs([
                ("ALFA", DIA, 500, ""),
                ("BETA", DIA, 0, "AGUARDANDO envio da Rute"),
            ]),
        )
        self.assertEqual(faltando, ["BETA"])

    def test_marcador_de_aguardando_ignora_acento_e_caixa(self):
        # A observação é digitada à mão; exigir grafia exata faria a regra
        # falhar em silêncio (volta ao comportamento antigo, sem aviso).
        for obs in ("aguardando", "Pendente", "não enviou ainda",
                    "NAO ENVIADO ATE AGORA", "  Aguardando  "):
            with self.subTest(obs=obs):
                faltando = self._rodar(
                    _plano("BETA"),
                    _producao_obs([("BETA", DIA, 0, obs)]),
                )
                self.assertEqual(faltando, ["BETA"])

    def test_producao_real_vence_o_marcador(self):
        # Se veio número, o dado chegou — a observação não retira o lançamento.
        faltando = self._rodar(
            _plano("BETA"),
            _producao_obs([("BETA", DIA, 800, "aguardando conferencia do saldo")]),
        )
        self.assertEqual(faltando, [])

    def test_lancamento_parcial_no_dia_encerra_a_cobranca(self):
        # Mesma facção com duas linhas: uma aguardando, outra com produção.
        # O dado do dia chegou, então não há o que cobrar.
        faltando = self._rodar(
            _plano("BETA"),
            _producao_obs([
                ("BETA", DIA, 0, "AGUARDANDO o restante"),
                ("BETA", DIA, 300, ""),
            ]),
        )
        self.assertEqual(faltando, [])

    def test_pendencias_saem_ordenadas(self):
        # O e-mail lista as pendências direto — ordem instável faria o
        # `detalhe` salvo em `EnvioDiario` variar à toa entre envios
        # (ver relatorios/cron.py).
        anteontem = date(2026, 8, 4)
        faltando = self._rodar(
            _plano("ZETA", "ALFA", "BETA"),
            # todas conhecidas do histórico, nenhuma lançou no dia checado
            _producao([("ZETA", anteontem), ("ALFA", anteontem), ("BETA", anteontem)]),
        )
        self.assertEqual(faltando, ["ALFA", "BETA", "ZETA"])
