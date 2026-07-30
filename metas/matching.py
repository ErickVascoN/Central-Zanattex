"""
Casa nomes da BD Plano de Metas (Responsável/Produto/Cliente) com os nomes
canônicos usados na produção real já sincronizada (`producao_faccoes.FACCAO`/
`.PRODUTO`/`.CLIENTE`, ou a unidade interna LITTEX em `producao_interno`) —
pra calcular o Realizado automático em vez de confiar no lançamento manual
da planilha (o usuário não confia 100% nele).

Regra de ouro (confirmada com o usuário): se o produto/cliente não aparece na
produção real, é porque genuinamente não foi produzido — tudo bem, mostra
zero. O que NUNCA pode acontecer é alguém que produziu de verdade não ter
essa produção puxada só por causa de uma pequena diferença de grafia entre
as duas planilhas. Por isso a aproximação por nome (fuzzy) é generosa aqui,
mas nunca cega: nomes de empresa/facção nunca casam por fuzzy com nome de
pessoa (evita juntar duas coisas erradas).
"""

from __future__ import annotations

from difflib import SequenceMatcher

from integracao.fontes import FACCOES_FACCAO_ALIAS, FACCOES_PRODUTO_ALIAS, FACCOES_CLIENTE_ALIAS
from integracao.normalize import normalize_text

LITTEX_SENTINEL = "__LITTEX__"

_ALIAS_FACCAO_NORM = {normalize_text(k): v for k, v in FACCOES_FACCAO_ALIAS.items()}
_ALIAS_PRODUTO_NORM = {normalize_text(k): v for k, v in FACCOES_PRODUTO_ALIAS.items()}
_ALIAS_CLIENTE_NORM = {normalize_text(k): v for k, v in FACCOES_CLIENTE_ALIAS.items()}

# Nomes de empresa/facção conhecidos — nunca entram na aproximação fuzzy de
# pessoa (evita casar "GIATTEX" com algum prestador de nome parecido, etc.).
_NOMES_EMPRESA_NORM = {
    normalize_text(n) for n in (
        "GIATTEX", "GGTTEX RUTE", "GGTTEX CORTINA", "PREVITTEX MATRIZ",
        "MEGA PREVEN MATRIZ", "MEGA BARIRI", "MEGA (BOCA)", "MEGA (CARLINE)",
        "LITEX", "QUARTERIZADAS",
    )
}


def _parece_pessoa(nome_norm: str) -> bool:
    return nome_norm not in _NOMES_EMPRESA_NORM


def _similaridade(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _sem_plural(s: str) -> str:
    return s[:-1] if len(s) > 4 and s.endswith("S") else s


def resolver_responsavel(
    responsavel: str, produto: str, nomes_producao: set[str],
) -> str | None:
    """Retorna o nome canônico em `nomes_producao` (ou `LITTEX_SENTINEL`
    pra unidade interna LITTEX), ou None se não achar correspondência
    confiável."""
    n = normalize_text(responsavel)
    if not n:
        return None

    if n == normalize_text("LITEX"):
        return LITTEX_SENTINEL

    if n == normalize_text("GGTTEX") and "CORTINA" in normalize_text(produto):
        return "GGTTEX CORTINA" if "GGTTEX CORTINA" in nomes_producao else None

    if n in _ALIAS_FACCAO_NORM:
        candidato = _ALIAS_FACCAO_NORM[n]
        if candidato in nomes_producao:
            return candidato

    for real in nomes_producao:
        if normalize_text(real) == n:
            return real

    if not _parece_pessoa(n):
        return None  # nome de empresa sem match exato/alias: não força fuzzy

    melhor, melhor_score = None, 0.0
    for real in nomes_producao:
        real_norm = normalize_text(real)
        if not _parece_pessoa(real_norm):
            continue
        if real_norm in n or n in real_norm:
            return real  # nome contido no outro (nome completo x nome curto)
        score = _similaridade(n, real_norm)
        if score > melhor_score:
            melhor, melhor_score = real, score
    if melhor_score >= 0.82:
        return melhor
    return None


def resolver_produto(produto: str, candidatos: set[str]) -> str | None:
    """Casa o nome de produto do plano com um dos `candidatos` (produtos
    realmente produzidos por aquele prestador/facção no mês). None quando
    não há correspondência razoável — nesse caso é genuinamente zero."""
    n = normalize_text(produto)
    if not n or not candidatos:
        return None

    if n in _ALIAS_PRODUTO_NORM:
        alvo = normalize_text(_ALIAS_PRODUTO_NORM[n])
        for c in candidatos:
            if normalize_text(c) == alvo:
                return c

    for c in candidatos:
        if normalize_text(c) == n:
            return c

    n_sem_s = _sem_plural(n)
    for c in candidatos:
        c_norm = _sem_plural(normalize_text(c))
        if c_norm == n_sem_s:
            return c

    # Sem substring aqui de propósito: "FRONHA HOTEL" contém "FRONHA", mas
    # "FRONHA" sozinho é uma categoria genérica (existem vários tipos de
    # fronha) — um substring curto assim erra o produto específico em vez
    # de acertar. Mesma razão pra exigir o MESMO conjunto de palavras antes
    # de aceitar por similaridade: "JOGO DE CAMA PP" (Ponto Palito) tem uma
    # palavra a mais que "JOGO DE CAMA" genérico — é um produto diferente,
    # não uma variação de grafia, então nunca deve casar por fuzzy só
    # porque o texto é parecido (confirmado com o usuário: PP = Ponto
    # Palito, um tipo de jogo de cama distinto). O fuzzy só serve pra pegar
    # erro de digitação/plural dentro do MESMO conjunto de palavras — prefere
    # marcar como não identificado a atribuir errado.
    n_palavras = set(n.split())
    melhor, melhor_score = None, 0.0
    for c in candidatos:
        c_norm = normalize_text(c)
        if set(c_norm.split()) != n_palavras:
            continue
        score = _similaridade(n, c_norm)
        if score > melhor_score:
            melhor, melhor_score = c, score
    if melhor_score >= 0.87:
        return melhor
    return None


def resolver_cliente(cliente: str, candidatos: set[str]) -> str | None:
    """Mesma lógica de `resolver_produto`, pro lado do cliente (usa o alias
    de cliente já existente — `FACCOES_CLIENTE_ALIAS` — em vez do de
    produto)."""
    n = normalize_text(cliente)
    if not n or not candidatos:
        return None

    if n in _ALIAS_CLIENTE_NORM:
        alvo = normalize_text(_ALIAS_CLIENTE_NORM[n])
        for c in candidatos:
            if normalize_text(c) == alvo:
                return c

    for c in candidatos:
        if normalize_text(c) == n:
            return c

    melhor, melhor_score = None, 0.0
    for c in candidatos:
        score = _similaridade(n, normalize_text(c))
        if score > melhor_score:
            melhor, melhor_score = c, score
    if melhor_score >= 0.82:
        return melhor
    return None
