"""
Importador do relatório de Carteira de Pedidos exportado direto do ERP
(Excel), substituindo o passo manual de colar os dados numa planilha do
Google Sheets antes de sincronizar. Produz o MESMO formato de saída que
`carteira.servicos.carregar_carteira_do_sheets()` já produz — os dois
caminhos alimentam a mesma tabela sincronizada `carteira_pedidos` (ver
`integracao.db_sync.sync_dataframe`) e convivem: a planilha continua
funcionando do jeito de sempre, este é só outro jeito de chegar lá.

Confirmado com um export real do ERP (2026-09, arquivo "Carteira_de_
pedidos_11-09.xls" — na verdade um .xlsx zipado com extensão antiga,
`pandas.read_excel` lê sem precisar de `xlrd`): os cabeçalhos batem
EXATAMENTE com o mapeamento posicional que `carregar_carteira_do_sheets()`
já assume — inclusive as colunas hoje ignoradas (CFOP, Frete, ST, "Venc.
1ª Fatura", esta última sempre um placeholder "12/30/1899" sem informação
real). Ou seja, a planilha do Sheets historicamente foi alimentada colando
este mesmo relatório inteiro.

Diferente do parser da planilha (que lê por POSIÇÃO fixa de coluna, porque
o CSV do Sheets não tem cabeçalho confiável), este lê por NOME de coluna —
mais robusto a uma reordenação futura do relatório do ERP — e nunca
descarta uma linha em silêncio: toda linha ignorada vira um aviso
explicado, devolvido junto com o DataFrame."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import servicos

# Cabeçalho esperado do relatório do ERP -> campo interno (mesmo
# vocabulário que carregar_carteira_do_sheets() já usa). Colunas do
# relatório que não aparecem aqui (CFOP, Frete, ST, "Venc. 1ª Fatura") são
# ignoradas de propósito — hoje sem informação útil (valores constantes no
# export real), mesma decisão que o parser da planilha já toma.
_ALIAS_CABECALHO = {
    "DATA EMISSAO": "DATA",
    "NOTA": "NOTA",
    "PEDIDO": "PEDIDO",
    "DESTINATARIO": "DESTINATARIO",
    "MUNICIPIO": "MUNICIPIO",
    "VENDEDOR": "VENDEDOR",
    "QUANTIDADE": "QUANTIDADE",
    "VALOR UNIT.": "VALOR_UNIT",
    "VALOR TOTAL": "VALOR_TOTAL",
    "COD. PROD.": "COD_PROD",
    "DESCRICAO DO PRODUTO": "DESCRICAO",
    "CENTRO DE CUSTO": "CENTRO_CUSTO",
}

# Sem essas, uma linha não tem como virar registro válido.
_COLUNAS_OBRIGATORIAS = ["DATA", "PEDIDO", "QUANTIDADE", "VALOR_TOTAL"]

# Colunas que aparecem no export real do ERP mas hoje não carregam
# informação útil (valores constantes) — mesma decisão que o parser da
# planilha (`servicos.carregar_carteira_do_sheets`) já toma ao pular esses
# índices. Diferente de uma coluna REALMENTE desconhecida (ver
# `_mapear_colunas`), estas são esperadas e silenciosas, não viram aviso.
_COLUNAS_IGNORADAS_CONHECIDAS = {"CFOP", "FRETE", "ST", "VENC. 1 FATURA"}

# Nas primeiras N linhas do arquivo procuramos o cabeçalho de verdade — o
# relatório do ERP tem linhas de título em branco antes dele.
_MAX_LINHAS_BUSCA_CABECALHO = 10


class CabecalhoNaoEncontrado(ValueError):
    """O arquivo não tem, nas primeiras linhas, uma linha com PEDIDO e
    QUANTIDADE — ou não é o relatório certo, ou o layout mudou demais pro
    parser reconhecer. Mensagem já pronta pra mostrar na tela."""


@dataclass
class ResultadoImportacao:
    df: pd.DataFrame
    avisos: list[str] = field(default_factory=list)

    @property
    def linhas_importadas(self) -> int:
        return len(self.df)

    @property
    def linhas_ignoradas(self) -> int:
        return len(self.avisos)


def _localizar_cabecalho(df_bruto: pd.DataFrame) -> int:
    """Acha a linha de cabeçalho de verdade nas primeiras
    `_MAX_LINHAS_BUSCA_CABECALHO` linhas — mesmo padrão que
    `programacao.servicos.carregar_programacao_do_sheets` já usa (procura
    por CONTEÚDO, não por posição fixa, porque o relatório do ERP tem
    linhas de título antes do cabeçalho de verdade)."""
    limite = min(_MAX_LINHAS_BUSCA_CABECALHO, len(df_bruto))
    for i in range(limite):
        valores = {servicos._norm(v) for v in df_bruto.iloc[i] if pd.notna(v)}
        if "PEDIDO" in valores and "QUANTIDADE" in valores:
            return i
    raise CabecalhoNaoEncontrado(
        'Não encontrei o cabeçalho do relatório (esperava as colunas "Pedido" '
        'e "Quantidade") nas primeiras linhas do arquivo. Confira se é o '
        "export certo da Carteira de Pedidos."
    )


def _mapear_colunas(cabecalho: list) -> tuple[dict[int, str], list[str]]:
    """Casa cada célula do cabeçalho (por NOME normalizado) com o campo
    interno correspondente. Retorna {índice_da_coluna: campo} + avisos
    para colunas repetidas — o relatório pode ganhar/perder coluna sem
    travar o importador, só as 4 obrigatórias (`_COLUNAS_OBRIGATORIAS`)
    precisam estar presentes."""
    mapa: dict[int, str] = {}
    avisos: list[str] = []
    vistos: set[str] = set()
    for idx, valor in enumerate(cabecalho):
        if pd.isna(valor):
            continue
        nome_norm = servicos._norm(valor)
        campo = _ALIAS_CABECALHO.get(nome_norm)
        if campo is None:
            if nome_norm not in _COLUNAS_IGNORADAS_CONHECIDAS:
                avisos.append(f'Coluna desconhecida no relatório, ignorada: "{valor}".')
            continue
        if campo in vistos:
            avisos.append(f'Coluna "{valor}" repetida no relatório — usada a primeira ocorrência.')
            continue
        mapa[idx] = campo
        vistos.add(campo)

    faltando = [c for c in _COLUNAS_OBRIGATORIAS if c not in vistos]
    if faltando:
        raise CabecalhoNaoEncontrado(
            f"O relatório não tem a(s) coluna(s) obrigatória(s): {', '.join(faltando)}."
        )
    return mapa, avisos


def _texto_de_numero(valor) -> str:
    """Converte uma célula que pode vir como int, float (a coluna inteira é
    promovida a float pelo pandas quando tem NaN misturado) ou string
    alfanumérica pra texto, sem deixar sobra de ".0" quando o valor é
    inteiro — usado em PEDIDO, NOTA e COD. PROD., que no relatório do ERP
    tanto vêm puramente numéricos quanto alfanuméricos (ex.: o Cod. Prod.
    às vezes é "1.72613.01.9999")."""
    if pd.isna(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _texto(valor) -> str:
    """Célula de texto comum (Destinatário, Município, ...) — NaN vira
    string vazia em vez de virar a string literal "nan"."""
    return "" if pd.isna(valor) else str(valor).strip()


def parse_excel_carteira(arquivo_ou_caminho) -> ResultadoImportacao:
    """Lê o .xlsx exportado do ERP e devolve o MESMO formato de saída que
    `servicos.carregar_carteira_do_sheets()` produz — pronto pra
    `integracao.db_sync.sync_dataframe`. `arquivo_ou_caminho` é qualquer
    coisa que `pandas.read_excel` aceite (caminho no disco, file-like de
    upload)."""
    try:
        df_bruto = pd.read_excel(arquivo_ou_caminho, sheet_name=0, header=None)
    except Exception as e:
        raise CabecalhoNaoEncontrado(f"Não consegui ler o arquivo como Excel: {e}") from None

    if df_bruto.empty:
        raise CabecalhoNaoEncontrado("O arquivo está vazio.")

    linha_cabecalho = _localizar_cabecalho(df_bruto)
    mapa_colunas, avisos = _mapear_colunas(list(df_bruto.iloc[linha_cabecalho]))
    dados = df_bruto.iloc[linha_cabecalho + 1:].reset_index(drop=True)

    registros = []
    for i, row in dados.iterrows():
        # Número da linha tal como apareceria se abrisse o Excel de novo —
        # cabeçalho é 1-based e já foi contado, daí o +2 (não +1).
        linha_excel = linha_cabecalho + 2 + i
        bruto = {campo: row[idx] for idx, campo in mapa_colunas.items()}

        if all(pd.isna(v) for v in row):
            continue  # linha totalmente vazia (comum no fim do arquivo) — ruído, não aviso

        pedido = _texto_de_numero(bruto.get("PEDIDO"))
        if not pedido:
            avisos.append(f"Linha {linha_excel}: sem PEDIDO — ignorada.")
            continue

        data_bruta = bruto.get("DATA")
        if pd.isna(data_bruta):
            avisos.append(f"Linha {linha_excel} (pedido {pedido}): sem data — ignorada.")
            continue
        try:
            dt = pd.to_datetime(data_bruta).date()
        except Exception:
            avisos.append(f"Linha {linha_excel} (pedido {pedido}): data inválida ({data_bruta!r}) — ignorada.")
            continue

        qt = servicos._parse_float(bruto.get("QUANTIDADE", 0))
        vt = servicos._parse_float(bruto.get("VALOR_TOTAL", 0))
        vu = servicos._parse_float(bruto.get("VALOR_UNIT", 0))
        if qt <= 0 and vt <= 0:
            avisos.append(f"Linha {linha_excel} (pedido {pedido}): quantidade e valor total zerados — ignorada.")
            continue

        mun = _texto(bruto.get("MUNICIPIO"))
        desc = _texto(bruto.get("DESCRICAO"))
        cc = _texto(bruto.get("CENTRO_CUSTO"))

        registros.append({
            "DATA": dt, "PEDIDO": pedido, "NOTA": _texto_de_numero(bruto.get("NOTA")),
            "DESTINATARIO": _texto(bruto.get("DESTINATARIO")), "MUNICIPIO": mun,
            "ESTADO": servicos._estado(mun),
            "VENDEDOR": _texto(bruto.get("VENDEDOR")),
            "QUANTIDADE": qt, "VALOR_UNIT": vu, "VALOR_TOTAL": vt,
            "COD_PROD": _texto_de_numero(bruto.get("COD_PROD")),
            "DESCRICAO": desc, "CATEGORIA": servicos._categorizar(desc),
            "TAMANHO": servicos._tamanho(desc), "SUBCATEGORIA": servicos._subcategoria(desc),
            "CENTRO_CUSTO": cc if cc else "N/I",
        })

    if not registros:
        return ResultadoImportacao(df=pd.DataFrame(), avisos=avisos)

    df = pd.DataFrame(registros)
    df["DATA"] = pd.to_datetime(df["DATA"])
    df["ANO"] = df["DATA"].dt.year
    df["MES"] = df["DATA"].dt.month
    df["ANO_MES"] = df["DATA"].dt.to_period("M").astype(str)
    df["MES_LABEL"] = df["DATA"].apply(lambda d: f"{servicos.MESES_PT_ABR[d.month]}/{str(d.year)[2:]}")
    df["CLIENTE"] = df["DESTINATARIO"].apply(lambda x: servicos._ALIAS_CLIENTE.get(x.upper(), x))
    df["CLIENTE_CURTO"] = df["CLIENTE"].apply(servicos._nome_curto)

    return ResultadoImportacao(df=df, avisos=avisos)
