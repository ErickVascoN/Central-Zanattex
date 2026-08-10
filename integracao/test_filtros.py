"""Testes dos filtros conexos da toolbar (`integracao.filtros`).

Duas classes de erro que estes testes travam, ambas SILENCIOSAS — a página
carrega normal e ninguém percebe:

1. Um menu deixar de refletir os outros (o bug original: escolher a Categoria
   "Lençol" e o menu Produto seguir listando os 387 produtos, fronha inclusive).
2. Marcador de ausência da planilha virar opção — checkbox "-" ou em branco,
   que não diz nada e, pior, não devolve nada quando marcado.
"""

import pandas as pd
from django.test import SimpleTestCase

from . import filtros

CAMPOS = [
    {"name": "categorias", "label": "Categoria", "col": "CATEGORIA"},
    {"name": "produtos", "label": "Produto", "col": "PRODUTO"},
    {"name": "tamanhos", "label": "Tamanho", "col": "TAMANHO"},
]


def _df():
    """3 categorias; cada produto só existe dentro da sua."""
    return pd.DataFrame([
        {"CATEGORIA": "LENCOL", "PRODUTO": "JG CASAL", "TAMANHO": "CASAL"},
        {"CATEGORIA": "LENCOL", "PRODUTO": "JG QUEEN", "TAMANHO": "QUEEN"},
        {"CATEGORIA": "FRONHA", "PRODUTO": "KIT FRONHA", "TAMANHO": "CASAL"},
        {"CATEGORIA": "CORTINA", "PRODUTO": "CORT LISA", "TAMANHO": "-"},
    ])


def _ok(prep, campo):
    """Valores que o menu `campo` ainda oferece, ordenados."""
    return sorted(o["valor"] for f in prep["filtros"] if f["name"] == campo
                  for o in f["opcoes"] if o["ok"])


def _todas(prep, campo):
    """Tudo que foi renderizado no menu `campo` — visível ou não."""
    return [o["valor"] for f in prep["filtros"] if f["name"] == campo for o in f["opcoes"]]


class FiltrosConexosTests(SimpleTestCase):

    def test_sem_selecao_mostra_tudo(self):
        prep = filtros.preparar(_df(), CAMPOS, {})
        self.assertEqual(_ok(prep, "produtos"),
                         ["CORT LISA", "JG CASAL", "JG QUEEN", "KIT FRONHA"])

    def test_categoria_restringe_produto(self):
        """O bug original: Produto tem que seguir a Categoria escolhida."""
        prep = filtros.preparar(_df(), CAMPOS, {"categorias": ["LENCOL"]})
        self.assertEqual(_ok(prep, "produtos"), ["JG CASAL", "JG QUEEN"])
        self.assertNotIn("KIT FRONHA", _ok(prep, "produtos"))

    def test_menu_nao_restringe_a_si_mesmo(self):
        """Marcar "Lençol" não pode sumir com "Cortina" do próprio menu —
        senão não dá pra somar uma 2ª categoria."""
        prep = filtros.preparar(_df(), CAMPOS, {"categorias": ["LENCOL"]})
        self.assertEqual(_ok(prep, "categorias"), ["CORTINA", "FRONHA", "LENCOL"])

    def test_duas_selecoes_somam(self):
        prep = filtros.preparar(_df(), CAMPOS, {"categorias": ["LENCOL", "FRONHA"]})
        self.assertEqual(_ok(prep, "produtos"), ["JG CASAL", "JG QUEEN", "KIT FRONHA"])

    def test_cruzamento_usa_todos_os_outros(self):
        prep = filtros.preparar(_df(), CAMPOS,
                                {"categorias": ["LENCOL"], "tamanhos": ["CASAL"]})
        self.assertEqual(_ok(prep, "produtos"), ["JG CASAL"])

    def test_marcado_incompativel_continua_visivel(self):
        """Senão o usuário não teria como desmarcar e destravar a tela."""
        prep = filtros.preparar(_df(), CAMPOS,
                                {"categorias": ["LENCOL"], "produtos": ["KIT FRONHA"]})
        self.assertIn("KIT FRONHA", _ok(prep, "produtos"))


class PlaceholderTests(SimpleTestCase):
    """Marcador de ausência não vira opção — as linhas seguem nos totais."""

    def test_traco_nao_vira_opcao(self):
        """Caso real: a Iacanga usa "-" no Tamanho dos Derivados."""
        self.assertNotIn("-", _todas(filtros.preparar(_df(), CAMPOS, {}), "tamanhos"))

    def test_variantes_de_ausencia(self):
        df = pd.DataFrame([{"CATEGORIA": "A", "PRODUTO": p, "TAMANHO": "CASAL"}
                           for p in ["REAL", "-", "", "NAN", "nan", "N/A", "None", "...", "  "]])
        self.assertEqual(_todas(filtros.preparar(df, CAMPOS, {}), "produtos"), ["REAL"])

    def test_placeholder_em_lista_fixa(self):
        campos = [{"name": "status", "label": "Status", "col": "CATEGORIA",
                   "valores": ["Pendente", "-", ""]}]
        prep = filtros.preparar(_df(), campos, {})
        self.assertEqual(_todas(prep, "status"), ["Pendente"])

    def test_linhas_com_placeholder_nao_somem_da_base(self):
        """Some da lista de opções, não dos dados: a Cortina (TAMANHO="-")
        tem que continuar aparecendo no menu Categoria."""
        prep = filtros.preparar(_df(), CAMPOS, {})
        self.assertIn("CORTINA", _ok(prep, "categorias"))


class ContarDistintosTests(SimpleTestCase):
    """Marcador de ausência não é "+1 produto"/"+1 cor" nos KPIs. Casos reais:
    a Iacanga usa "-" no Produto dos Derivados, o Arealva tem "..." na Cor e a
    Carteira tem item sem código — cada um inflava um contador em 1."""

    def test_ignora_placeholders(self):
        s = pd.Series(["Celta", "Liso", "-", "", "NAN", None, "Celta"])
        self.assertEqual(filtros.contar_distintos(s), 2)

    def test_sem_placeholder_conta_igual_ao_nunique(self):
        s = pd.Series(["Celta", "Liso", "Celta"])
        self.assertEqual(filtros.contar_distintos(s), s.nunique())

    def test_excluir_rotulo_do_modulo(self):
        """"SEM OP" é rótulo do Corte, não um marcador genérico."""
        s = pd.Series(["991", "992", "SEM OP", "-"])
        self.assertEqual(filtros.contar_distintos(s, excluir=("SEM OP",)), 2)
        self.assertEqual(filtros.contar_distintos(s), 3)

    def test_serie_vazia_ou_so_placeholder(self):
        self.assertEqual(filtros.contar_distintos(pd.Series([], dtype=object)), 0)
        self.assertEqual(filtros.contar_distintos(pd.Series(["-", "", None])), 0)

    def test_serve_como_agregador_de_groupby(self):
        """É usado direto em `.agg()` no resumo por OP do Corte."""
        df = pd.DataFrame({"OP": ["A", "A", "B"], "COR": ["AZUL", "-", "ROSA"]})
        contagem = df.groupby("OP").agg(n=("COR", filtros.contar_distintos))
        self.assertEqual(contagem.loc["A", "n"], 1)
        self.assertEqual(contagem.loc["B", "n"], 1)


class OpcaoSemCruzamentoTests(SimpleTestCase):
    """Opção que não aparece em nenhuma combinação não tem como ser cruzada —
    sem evidência, não esconde (senão sumiria sozinha da tela)."""

    def test_valor_fixo_ausente_da_base_continua_visivel(self):
        campos = [
            {"name": "categorias", "label": "Categoria", "col": "CATEGORIA"},
            {"name": "status", "label": "Status", "col": "TAMANHO",
             "valores": ["CASAL", "INEXISTENTE"]},
        ]
        prep = filtros.preparar(_df(), campos, {"categorias": ["LENCOL"]})
        self.assertIn("INEXISTENTE", _ok(prep, "status"))
