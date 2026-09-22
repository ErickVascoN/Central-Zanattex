"""
Extração de referência à NF de entrada a partir de texto livre — porta pro
Django a lógica de conciliação que um funcionário do fiscal criou num
protótipo à parte (controle-tecidos-nfe-arquivo-unico-6.html, JS local),
reimplementada aqui em cima do nosso modelo (Postgres, `saldo_atual`
decrementado transacionalmente em vez de recalculado do zero a cada vez).

Usado por fiscal/matching.py quando o campo `infAdProd` do item de saída não
basta sozinho: tenta achar o número (ou a chave de acesso) da NF de entrada
também na descrição do item, na informação complementar da nota inteira, e
na lista de NF-e formalmente referenciadas (`ide/NFref`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Termos que, perto de um número, indicam com confiança que é uma referência
# a NF de entrada ("forte") ou uma referência mais indireta ("fraco").
_TERMOS_FORTES = (
    "NOTAS FISCAIS ELETRONICAS", "NOTA FISCAL ELETRONICA", "NOTAS FISCAIS",
    "NOTA FISCAL", "NOTAS", "NOTA", "NFES", "NFE", "NFS", "NF", "DANFE",
)
_TERMOS_FRACOS = (
    "REFERENTES", "REFERENTE", "REFERENCIAS", "REFERENCIA", "REFS", "REF",
    "ORIGEM", "ENTRADA",
)

# Palavras que, perto de um número, indicam que ele NÃO é uma NF (é lote,
# cor, quantidade, unidade, código...) — evita falso positivo.
_PALAVRAS_BLOQUEADAS = {
    "LOTE", "LOTES", "PEDIDO", "PED", "OP", "OF", "COR", "CORES", "QTD", "QTDE",
    "QUANTIDADE", "ITEM", "ITENS", "COD", "CODIGO", "PECAS", "PECA", "PCS", "KG",
    "KGS", "MT", "MTS", "METROS", "ROLO", "ROLOS", "DATA", "DIA", "EMITIDA",
    "EMISSAO", "VALOR", "VL", "CFOP", "NCM", "CNPJ", "CPF", "IE", "PROTOCOLO",
    "CHAVE", "CONTRATO", "ORDEM", "OS", "PROCESSO", "VOLUME", "VOLUMES", "CAIXA",
    "CAIXAS", "TAM", "TAMANHO", "GRADE", "REFERENCIA", "MODELO", "ARTIGO",
}

_RE_CHAVE = re.compile(r"\d{44}")
_RE_TERMO_FORTE = re.compile(r"\b(?:" + "|".join(_TERMOS_FORTES) + r")\b", re.IGNORECASE)
_RE_TERMO_FRACO = re.compile(r"\b(?:" + "|".join(_TERMOS_FRACOS) + r")\b", re.IGNORECASE)
_RE_NUMERO = re.compile(r"\d{1,9}")
_RE_UNIDADE_OU_DATA_DEPOIS = re.compile(r"^\s*(?:[/\-]\s*\d|KG|KGS|MT|MTS|M2|UN|PC|PCS|RL)", re.IGNORECASE)
_RE_SO_NUMERO = re.compile(r"^\s*0*(\d{1,9})(?:\s*[/\-]\s*\d{1,3})?\s*$")


def contem_termo_referencia(texto: str) -> bool:
    """True se o texto cita algo como "NF"/"REF"/"ORIGEM", mesmo que
    `extrair_referencias` não tenha conseguido tirar um número seguro dali —
    diferencia "não tem nada a ver com NF" (SEM_REFERENCIA) de "parece
    citar uma NF mas o número não ficou claro" (REF_NAO_IDENTIFICADA)."""
    if not texto:
        return False
    return bool(_RE_TERMO_FORTE.search(texto) or _RE_TERMO_FRACO.search(texto))


def chave_valida(chave: str) -> bool:
    """Dígito verificador (módulo 11) da chave de acesso de 44 dígitos da NF-e."""
    if not re.fullmatch(r"\d{44}", chave):
        return False
    soma, peso = 0, 2
    for c in reversed(chave[:43]):
        soma += int(c) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    dv = 0 if resto < 2 else 11 - resto
    return dv == int(chave[43])


@dataclass
class ReferenciaEncontrada:
    numero: str  # número da NF (sem zeros à esquerda) ou a chave de 44 dígitos
    eh_chave: bool = False


def extrair_referencias(texto: str) -> list[ReferenciaEncontrada]:
    """Acha candidatos a "número de NF de entrada" dentro de texto livre.
    Prioridade: chave de acesso válida > campo que é só um número > termo
    forte (NF/NFE/NOTA FISCAL/DANFE) perto de um número > termo fraco
    (REF/ORIGEM/ENTRADA...) — nunca aceita um número colado a uma palavra da
    lista de bloqueio, nem seguido de unidade/data (sinal de que é
    quantidade, não referência)."""
    if not texto:
        return []
    texto = texto.strip()
    encontrados: list[ReferenciaEncontrada] = []
    vistos: set[str] = set()

    for chave in _RE_CHAVE.findall(texto):
        if chave_valida(chave) and chave not in vistos:
            vistos.add(chave)
            encontrados.append(ReferenciaEncontrada(chave, eh_chave=True))

    texto_sem_chave = _RE_CHAVE.sub(" ", texto)

    so_numero = _RE_SO_NUMERO.match(texto_sem_chave)
    if so_numero:
        numero = so_numero.group(1)
        if numero not in vistos:
            vistos.add(numero)
            encontrados.append(ReferenciaEncontrada(numero))
        return encontrados

    for regex in (_RE_TERMO_FORTE, _RE_TERMO_FRACO):
        for termo in regex.finditer(texto_sem_chave):
            resto = texto_sem_chave[termo.end():termo.end() + 40]
            num_match = _RE_NUMERO.search(resto)
            if not num_match or num_match.start() > 15:
                continue
            entre = re.split(r"\W+", resto[:num_match.start()].upper())
            if any(p in _PALAVRAS_BLOQUEADAS for p in entre):
                continue
            depois = resto[num_match.end():num_match.end() + 4]
            if _RE_UNIDADE_OU_DATA_DEPOIS.match(depois):
                continue
            numero = num_match.group().lstrip("0") or "0"
            if numero not in vistos:
                vistos.add(numero)
                encontrados.append(ReferenciaEncontrada(numero))

    return encontrados


_PALAVRAS_TECIDO = {"TECIDO", "TECIDOS", "TEC", "TC", "TCDO", "MALHA", "MALHAS"}


def parece_tecido(descricao: str) -> bool:
    """Reforço por palavra-chave além do NCM (que continua sendo o critério
    principal — ver eh_ncm_controlado) — se a descrição menciona "tecido"
    mas o NCM do item não está na lista controlada, vale a pena um humano
    conferir o cadastro antes de descartar o item como fora de escopo."""
    palavras = re.findall(r"[A-ZÀ-Ü]+", (descricao or "").upper())
    return any(p in _PALAVRAS_TECIDO for p in palavras)


# Grupos de unidade equivalentes — nunca converte KG↔M, só reconhece
# variações de escrita da mesma unidade.
_EQUIVALENCIAS_UNIDADE = {
    "KG": "KG", "KGS": "KG", "QUILO": "KG", "QUILOS": "KG", "KILO": "KG", "KILOS": "KG",
    "QUILOGRAMA": "KG", "QUILOGRAMAS": "KG",
    "M": "M", "MT": "M", "MTS": "M", "MTR": "M", "METRO": "M", "METROS": "M",
    "M2": "M2", "MT2": "M2",
    "UN": "UN", "UND": "UN", "UNID": "UN", "UNIDADE": "UN", "UNIDADES": "UN",
    "PC": "PC", "PCS": "PC", "PECA": "PC", "PECAS": "PC",
    "RL": "RL", "ROLO": "RL", "ROLOS": "RL",
}


def normalizar_unidade(unidade: str) -> str:
    u = (unidade or "").strip().upper()
    return _EQUIVALENCIAS_UNIDADE.get(u, u)


def unidades_compativeis(u1: str, u2: str) -> bool:
    return normalizar_unidade(u1) == normalizar_unidade(u2)


_PARAR_EM = {"E", "DE", "DA", "DO", "COM", "SEM", "PARA", "A", "O", "EM"}


def _tokens(descricao: str) -> list[str]:
    palavras = re.findall(r"[A-ZÀ-Ü0-9]+", (descricao or "").upper())
    return [p for p in palavras if p not in _PARAR_EM and len(p) > 1]


def descricoes_identicas(desc1: str, desc2: str) -> bool:
    return _tokens(desc1) == _tokens(desc2) and bool(_tokens(desc1))


def coeficiente_dice(desc1: str, desc2: str) -> float:
    """Similaridade por bigramas de caractere — mesma ideia do coeficiente
    de Dice usado no protótipo original pra achar descrições "parecidas"
    quando não bate nem por código nem por igualdade exata."""
    t1, t2 = _tokens(desc1), _tokens(desc2)
    if not t1 or not t2:
        return 0.0

    def bigramas(tokens: list[str]) -> set[str]:
        texto = " ".join(tokens)
        return {texto[i:i + 2] for i in range(len(texto) - 1)} or {texto}

    b1, b2 = bigramas(t1), bigramas(t2)
    return 2 * len(b1 & b2) / (len(b1) + len(b2))


def descricao_contida(descricao_curta: str, descricao_longa: str) -> bool:
    """True quando todas as palavras da descrição mais curta aparecem na
    outra — sinal de que uma é uma versão abreviada/genérica da outra."""
    curtos, longos = _tokens(descricao_curta), _tokens(descricao_longa)
    if not curtos:
        return False
    return all(p in longos for p in curtos)
