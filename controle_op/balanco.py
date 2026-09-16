"""Balanço de material — o "Material Reconciliation Report" da OP: o
material (kg pra Manta, kg-equivalente pra Lençol) que saiu do estoque está
todo explicado? A base é o que foi REQUISITADO (NF/PDF da OP, quando
alguém digitou) ou, na falta disso, o que foi CORTADO de fato (pesado/
medido no Corte) — nunca o programado, que é só expectativa em peças, sem
grandeza de material nenhuma.

Cada componente é kg (ou None quando falta dado pra calculá-lo — nunca 0:
dado ausente não pode virar perda inventada, senão o balanço mentiria
sobre onde foi parar o material). Cortina e Itaju não têm nenhuma grandeza
de material na fonte real (RegistroCorte não pesa/mede nada pra elas) — o
balanço nem tenta rodar pra essas unidades, devolve NAO_APLICAVEL sem
fingir uma pendência que não existe.

Peça boa (1ª) e 2ª qualidade vêm do RETORNO (confirmado fisicamente) —
`RetornoProducao` não tem campo de qualidade (isso já foi classificado na
Produção, ver controle_op/models.py), então o total retornado é repartido
na MESMA proporção que a Produção apontou. Sem nenhum retorno ainda, os
dois são 0 de verdade (nada a classificar), não dado faltante.

"Em processo" é o WIP acumulado dos 3 estágios (Corte→Envio→Produção→
Retorno) que ainda não fechou o ciclo — `cortado_pecas − retornado_pecas`
já soma os três de uma vez (telescópico: o que não foi enviado, mais o que
foi enviado mas não produzido, mais o que foi produzido mas não voltou)."""
from __future__ import annotations

from dataclasses import dataclass, field

from contas.models import UnidadeCorte
from corte.aproveitamento import UNIDADES_MANTA, calcular_aproveitamento
from corte.models import ProgramacaoCorte

from .producao import ProducaoAcumulada, ProducaoOP, calcular_producao, producao_por_op

# Peças de baby na planilha do Arealva (aba separada Baby/Retalho — ver
# [[manta-baby-retalho]]) viram kg com o mesmo fator histórico que o antigo
# RegistroCorte.extra["babys_pecas"] usava antes de virar campo em kg
# direto (corte/aproveitamento.py). Só serve pra converter ESSA fonte
# específica — RegistroCorte.baby_kg já é kg, não precisa de fator nenhum.
_FATOR_KG_BABY_PLANILHA = 0.1955

# Margem de tolerância pra fechar o balanço sem exigir batida exata — kg
# pesado em balança de chão/planilha manual sempre carrega um pouco de
# erro de arredondamento. Provisório: 2% do base, a calibrar contra
# balanços reais (mesmo espírito de LIMIAR_CONCLUIDO em corte/
# aproveitamento.py — número inicial, não dogma).
_TOLERANCIA_DIVERGENCIA_PCT = 0.02


class Base:
    REQUISITADO = "REQUISITADO"
    CORTADO = "CORTADO"
    INDISPONIVEL = "INDISPONIVEL"

    LABELS = {
        REQUISITADO: "Requisitado (NF/PDF da OP)",
        CORTADO: "Cortado (pesado/medido no Corte)",
        INDISPONIVEL: "Indisponível",
    }


class StatusBalanco:
    """FECHADO/DIVERGENTE só fazem sentido quando o ciclo terminou (nada
    "em processo" sobrando) — enquanto a OP ainda está correndo,
    EM_PROCESSO é o estado normal e esperado, não uma pendência a cobrar."""
    FECHADO = "FECHADO"
    DIVERGENTE = "DIVERGENTE"
    INCOMPLETO = "INCOMPLETO"
    EM_PROCESSO = "EM_PROCESSO"
    NAO_APLICAVEL = "NAO_APLICAVEL"

    LABELS = {
        FECHADO: "Fechado",
        DIVERGENTE: "Divergente",
        INCOMPLETO: "Incompleto",
        EM_PROCESSO: "Em processo",
        NAO_APLICAVEL: "Não aplicável",
    }


# Rótulo de cada componente pro "faltando" (o que a tela/PDF mostram como
# motivo de estar INCOMPLETO — dizer só "incompleto" sem dizer o quê não
# ajuda ninguém a resolver).
_ROTULO_COMPONENTE = {
    "peca_boa": "peça boa (1ª qualidade)",
    "segunda": "2ª qualidade",
    "em_processo": "material em processo",
    "retalho_corte": "retalho de corte",
    "baby": "baby",
    "reciclavel": "reciclável (plástico/tubo)",
    "retalho_producao": "retalho de produção",
}


@dataclass
class BalancoOP:
    status: str = StatusBalanco.NAO_APLICAVEL
    base: str | None = None
    base_kg: float | None = None

    peca_boa_kg: float | None = None
    segunda_kg: float | None = None
    em_processo_kg: float | None = None
    retalho_corte_kg: float | None = None
    # None pra Lençol de propósito — não é um componente que falta, é um
    # componente que não existe pra essa unidade (ver calcular_balanco).
    baby_kg: float | None = None
    reciclavel_kg: float | None = None
    retalho_producao_kg: float | None = None

    explicado_kg: float | None = None
    divergencia_kg: float | None = None
    divergencia_pct: float | None = None

    # Indicador de eficiência (o "Cutting Yield Analysis"), FORA do
    # critério de fechamento — quanto do material-base virou peça de 1ª,
    # não se a conta bateu. Pode existir mesmo com o balanço INCOMPLETO.
    aproveitamento_1a_qualidade_pct: float | None = None

    faltando: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return StatusBalanco.LABELS[self.status]

    @property
    def base_label(self) -> str:
        return Base.LABELS[self.base] if self.base else "—"


def calcular_balanco(programacao: ProgramacaoCorte, *, aproveitamento=None,
                     producao: ProducaoOP | None = None,
                     acumulada: ProducaoAcumulada | None = None) -> BalancoOP:
    unidade = programacao.unidade_corte
    if unidade not in UNIDADES_MANTA and unidade != UnidadeCorte.LENCOL:
        return BalancoOP(status=StatusBalanco.NAO_APLICAVEL)

    aproveitamento = aproveitamento or calcular_aproveitamento(programacao)
    if producao is None:
        producao = calcular_producao(programacao)
    acumulada = acumulada or producao_por_op(programacao)

    base, base_kg = _base(programacao, aproveitamento, unidade)

    cortado_pecas = aproveitamento.cortado_pecas
    retornado_pecas = producao.retornado_pecas
    produzido_total = acumulada.produzido_total

    em_processo_pecas = max(cortado_pecas - retornado_pecas, 0)

    if retornado_pecas <= 0:
        # Nada voltou ainda — não há o que classificar em 1ª/2ª, mas isso
        # é zero de verdade (nada aconteceu), não dado faltante.
        peca_boa_pecas, segunda_pecas = 0, 0
    elif produzido_total > 0:
        fracao_1a = acumulada.produzido_1a_total / produzido_total
        peca_boa_pecas = round(retornado_pecas * fracao_1a)
        segunda_pecas = retornado_pecas - peca_boa_pecas
    else:
        # Retornou algo, mas não há apontamento de Produção pra saber a
        # proporção — aqui sim é dado faltante (situação que já gera aviso
        # em RetornoProducaoForm.clean()).
        peca_boa_pecas, segunda_pecas = None, None

    conv = _conversor_pecas_kg(unidade, aproveitamento)

    # Mesma regra do peça boa/2ª acima: sem NENHUM apontamento de produção
    # ainda, o retalho de produção é 0 de verdade (nada foi produzido, não
    # tem como ter sobrado retalho) — só vira dado faltante quando HOUVE
    # apontamento e ninguém mediu o retalho em nenhum deles.
    retalho_producao_kg = (
        0.0 if acumulada.apontamentos == 0 else acumulada.retalho_producao_kg_total)

    componentes: dict[str, float | None] = {
        "peca_boa": conv(peca_boa_pecas),
        "segunda": conv(segunda_pecas),
        "em_processo": conv(em_processo_pecas),
        "retalho_corte": _retalho_corte_kg(programacao, aproveitamento, unidade),
        "retalho_producao": retalho_producao_kg,
    }
    if unidade in UNIDADES_MANTA:
        # Baby e reciclável só existem pra Manta — pra Lençol nem entram
        # no dict, então não contam como "faltando" (não são componentes
        # daquela unidade, e sim inaplicáveis).
        componentes["baby"] = _baby_kg(programacao, aproveitamento, unidade)
        componentes["reciclavel"] = _reciclavel_kg(aproveitamento)

    faltando = [_ROTULO_COMPONENTE[chave] for chave, valor in componentes.items() if valor is None]

    resultado = BalancoOP(
        base=base, base_kg=base_kg,
        peca_boa_kg=componentes["peca_boa"], segunda_kg=componentes["segunda"],
        em_processo_kg=componentes["em_processo"],
        retalho_corte_kg=componentes["retalho_corte"],
        baby_kg=componentes.get("baby"), reciclavel_kg=componentes.get("reciclavel"),
        retalho_producao_kg=componentes["retalho_producao"],
        faltando=faltando,
    )

    # Indicador de eficiência — não depende do balanço fechar, só de peça
    # boa e base existirem.
    if base_kg and componentes["peca_boa"] is not None:
        resultado.aproveitamento_1a_qualidade_pct = componentes["peca_boa"] / base_kg

    if base_kg is None or base_kg <= 0 or faltando:
        resultado.status = StatusBalanco.INCOMPLETO
        return resultado

    explicado = sum(componentes.values())
    resultado.explicado_kg = explicado
    resultado.divergencia_kg = base_kg - explicado
    resultado.divergencia_pct = resultado.divergencia_kg / base_kg

    if em_processo_pecas > 0:
        resultado.status = StatusBalanco.EM_PROCESSO
    elif abs(resultado.divergencia_pct) <= _TOLERANCIA_DIVERGENCIA_PCT:
        resultado.status = StatusBalanco.FECHADO
    else:
        resultado.status = StatusBalanco.DIVERGENTE

    return resultado


def _base(programacao: ProgramacaoCorte, aproveitamento, unidade: str) -> tuple[str, float | None]:
    if unidade in UNIDADES_MANTA:
        if programacao.kg_requisitado is not None:
            return Base.REQUISITADO, float(programacao.kg_requisitado)
        if aproveitamento.kg_cortado_total is not None:
            return Base.CORTADO, aproveitamento.kg_cortado_total
        return Base.INDISPONIVEL, None

    # Lençol — tudo em kg-equivalente (pra somar com retalho/retalho de
    # produção, que já são kg), então precisa de kg_por_metro pros dois
    # casos. Sem ele não dá pra converter REQUISITADO nem CORTADO.
    if aproveitamento.kg_por_metro is None:
        return Base.INDISPONIVEL, None
    if programacao.metros_requisitado is not None:
        return Base.REQUISITADO, float(programacao.metros_requisitado) * aproveitamento.kg_por_metro
    if aproveitamento.metros_programado is not None:
        return Base.CORTADO, aproveitamento.metros_programado * aproveitamento.kg_por_metro
    return Base.INDISPONIVEL, None


def _conversor_pecas_kg(unidade: str, aproveitamento):
    """Devolve uma função peças→kg pra esta unidade — None quando falta o
    fator de conversão (gramatura pra Manta; metros/peça + kg/metro pra
    Lençol), pra quem chamar propagar None em vez de inventar um número."""
    if unidade in UNIDADES_MANTA:
        fator = aproveitamento.gramatura_media
    else:
        tem_os_dois = (aproveitamento.metros_por_peca_medio is not None
                       and aproveitamento.kg_por_metro is not None)
        fator = (aproveitamento.metros_por_peca_medio * aproveitamento.kg_por_metro
                 if tem_os_dois else None)

    def converter(pecas: int | None) -> float | None:
        if pecas is None or fator is None:
            return None
        return pecas * fator

    return converter


def _retalho_corte_kg(programacao: ProgramacaoCorte, aproveitamento, unidade: str) -> float | None:
    if aproveitamento.retalho_kg_total is not None:
        return aproveitamento.retalho_kg_total
    if unidade == UnidadeCorte.AREALVA_MANTA:
        _, retalho = _baby_retalho_planilha_arealva(programacao)
        return retalho
    return None


def _baby_kg(programacao: ProgramacaoCorte, aproveitamento, unidade: str) -> float | None:
    if aproveitamento.baby_kg_total is not None:
        return aproveitamento.baby_kg_total
    if unidade == UnidadeCorte.AREALVA_MANTA:
        baby, _ = _baby_retalho_planilha_arealva(programacao)
        return baby
    return None


def _reciclavel_kg(aproveitamento) -> float | None:
    plastico = aproveitamento.plastico_kg_total
    tubo = aproveitamento.tubo_kg_total
    if plastico is None and tubo is None:
        return None
    return float(plastico or 0) + float(tubo or 0)


def _baby_retalho_planilha_arealva(programacao: ProgramacaoCorte) -> tuple[float | None, float | None]:
    """Fallback só pro Arealva, só quando ninguém preencheu baby_kg/
    retalho_kg no RegistroCorte ainda — lê a aba separada de Baby/Retalho
    que a Manta Arealva já alimenta hoje (ver [[manta-baby-retalho]] na
    memória). Casamento por OP é string bruta (`NUM OP` da planilha ==
    `pedido`, sem normalize_op) — risco conhecido de não casar
    silenciosamente (V5 do plano); aceito porque redesenhar essa fonte
    está fora desta leva de propósito. `try/except` amplo: é uma
    referência auxiliar, não pode derrubar o balanço se a tabela
    sincronizada estiver vazia/indisponível (mesmo padrão defensivo de
    `producao_diaria_auto`).

    Devolve (baby_kg, retalho_kg) — baby é None se a OP só tem lançamento
    "?" no período (não informado, não é zero); retalho nunca é None
    (a planilha já trata ausência como 0 nesse campo, na origem)."""
    try:
        from corte.servicos import carregar_baby_retalho
        df = carregar_baby_retalho()
    except Exception:
        return None, None
    if df is None or df.empty:
        return None, None

    linhas = df[df["OP"] == programacao.pedido]
    if linhas.empty and programacao.op_interna:
        linhas = df[df["OP"] == programacao.op_interna]
    if linhas.empty:
        return None, None

    baby_vals = linhas["BABY"].dropna()
    baby_kg = float(baby_vals.sum()) * _FATOR_KG_BABY_PLANILHA if not baby_vals.empty else None
    retalho_kg = float(linhas["RT"].sum())
    return baby_kg, retalho_kg
