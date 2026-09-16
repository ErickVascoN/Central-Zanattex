"""Cálculo de aproveitamento (previsto × realizado) e atualização de status
de uma ProgramacaoCorte a partir dos RegistroCorte lançados na Gestão de
Corte. Funções, não métodos de model — mesma separação que
programacao/servicos.py::enriquecer() já usa entre ORM e regra de negócio.

Fórmulas alinhadas com as planilhas de referência que a empresa já usa hoje
(enviadas pelo usuário em 2026-08-18):
- Manta/Cobertor (Manta Arealva, Manta Iacanga): "CONTROLE DE OPs AREALVA.xlsx"
  — rendimento é uma RECONCILIAÇÃO DE PESO, não peça/peça. `Kg Final` =
  peças cortadas × gramatura média (ponderada por peças, ver
  `_media_ponderada`) + retalho (kg) + baby (kg, `RegistroCorte.baby_kg` —
  antes era peças × fator fixo, agora é pesado direto igual ao retalho);
  `Divergência` = Kg Cortado (pesado na balança) − Kg Final; e o
  `Aproveitamento (%)` é Kg Final ÷ Kg Cortado — mede quanto do peso pesado
  "bate" com peça boa + retalho + baby (a diferença é perda de processo/
  erro de balança, não desvio de meta). Gramatura é campo numérico real do
  model (`RegistroCorte.gramatura`) desde a Fase 4 do mini-ERP da OP —
  registros antigos com `extra["gramatura"]` continuam lidos por
  compatibilidade (`_gramatura_de`).
- Lençol (Jogo de Cama, Fronhas, etc.): "INFORMAÇOES DE CORTES EM GERAL.xlsx"
  — aproveitamento base é peça/peça (Cortado ÷ Programado). Em metros, a
  reconciliação é a mesma ideia da Manta só que INVERTIDA: "Programado" é o
  que foi MEDIDO do rolo (`metros_cortado`, o cortador separa esse tanto de
  tecido pra trabalhar); "Cortado (Real)" é CALCULADO — quantidade útil ×
  metros por peça (pra jogo de cama, a quantidade útil é o CASADO, pares
  cima+fundo completos, não o lençol de cima bruto). A planilha também
  tinha uma "Perda (%)" com fórmula inconsistente (Esperados ÷ Diferença,
  quase sempre em branco) — não reproduzida aqui de propósito.

`kg/metros PREVISTO` (por OP, não só realizado) ainda não existe no sistema
— combinado com o usuário que essa parte fica de fora por enquanto (a
Programação de Corte não captura gramatura/metros-por-peça esperados na
criação da OP).

Conversão kg→equivalente em kg pra Lençol: preparada (campo opcional
`kg_por_metro` em `corte/forms.py`, só existe pra Lençol — a Manta não
mede rolo/peça em metros, só pesa em kg, não tem o que converter), mas
ninguém alimenta esse dado ainda — precisa do peso do tecido por metro
linear (função da largura do rolo + gramatura por m², não a mesma
gramatura por PEÇA que já capturamos). Assim que a empresa tiver esse
número, é só preencher no registro de corte que a conversão liga
sozinha."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from contas.models import UnidadeCorte

from .lencol_caseamento import eh_jogo_de_cama, fronha_mult
from .models import ProgramacaoCorte

# Mesmo limiar que programacao/servicos.py::_status_corte() já usa pro
# cruzamento com a planilha — centralizado aqui pra não duplicar o número
# mágico entre os dois lugares que decidem "isso é Concluído ou não".
LIMIAR_CONCLUIDO = 0.96

UNIDADES_MANTA = {UnidadeCorte.AREALVA_MANTA, UnidadeCorte.IACANGA_MANTA}


@dataclass
class Aproveitamento:
    saldo_pecas: int
    cortado_pecas: int
    pct_pecas: float | None
    retalho_pct: float | None
    status_calculado: str
    # Totais crus que o Balanço de material (Fase 4, controle_op/balanco.py)
    # precisa e que antes ficavam só locais em calcular_aproveitamento(),
    # descartados no retorno — nenhum dos dois é zero por padrão (ausência
    # de lançamento ≠ zero, mesma regra do resto do balanço).
    kg_cortado_total: float | None = None
    retalho_kg_total: float | None = None
    baby_kg_total: float | None = None
    plastico_kg_total: float | None = None
    tubo_kg_total: float | None = None
    gramatura_media: float | None = None
    # Manta/Cobertor — reconciliação de peso (None se não houver gramatura
    # informada em nenhum registro, ex.: OP sem corte lançado ainda).
    kg_final: float | None = None
    divergencia_kg: float | None = None
    aproveitamento_peso_pct: float | None = None
    # Lençol — reconciliação em metros (mesma ideia da Manta em peso, só
    # que os dois lados são invertidos: aqui quem é MEDIDO/real é o rolo
    # ("Programado" = metros_cortado, o que foi de fato retirado do rolo
    # pra trabalhar) e quem é CALCULADO é o resultado útil ("Cortado Real"
    # = quantidade útil × metros por peça — pra jogo de cama usa os
    # CASADOS, pares completos cima+fundo, não a quantidade bruta de
    # lençol de cima). None se não houver metros_por_peca/metros_cortado
    # informado em nenhum registro.
    metros_programado: float | None = None
    metros_cortado_real: float | None = None
    divergencia_metros: float | None = None
    aproveitamento_metros_pct: float | None = None
    # Média ponderada de metros por peça — exposta pro Balanço de material
    # converter peças em metros (o mesmo papel que `gramatura_media` faz
    # pra Manta). Antes só existia como variável local dentro de
    # `_calcular_lencol`, descartada no retorno (mesmo problema do V7).
    metros_por_peca_medio: float | None = None
    # Conversão pra kg equivalente — só Lençol (a Manta não mede em metros,
    # não tem o que converter). Só preenche se algum registro trouxer
    # `kg_por_metro` (kg de tecido por metro linear, não a gramatura por
    # peça) — ninguém alimenta esse campo hoje, deixado pronto pra quando
    # tiverem. Ver corte/forms.py::CAMPOS_EXTRA_POR_UNIDADE.
    kg_por_metro: float | None = None
    kg_convertidos: float | None = None       # metros_cortado * kg_por_metro
    # Caseamento Jogo × Fundo × Fronha (Lençol, só quando o produto da OP é
    # jogo de cama — duplo/simples). Cima, fundo e fronha são lançados
    # independentes (cima = quantidade_pecas; fundo/fronha = quanto o
    # cortador informou na Gestão de Corte) — CASADOS é o quanto realmente
    # dá pra montar de jogo completo, limitado pelo mais escasso dos três
    # (fronha entra na conta já dividida por fronha_mult, já que um caseado
    # duplo consome 2 fronhas e um simples consome 1). O que sobra de cada
    # um vira avulso — `cortado_pecas` acima já é o total de Lençol de cima
    # cortado.
    eh_jogo_de_cama: bool = False
    fundo_cortado: int | None = None
    fronha_cortada: int | None = None
    casados: int | None = None                # min(cima, fundo, fronha // fronha_mult) entre o que já foi lançado
    avulsos_cima: int | None = None           # cima_cortado - casados
    avulsos_fundo: int | None = None          # fundo_cortado - casados
    avulsos_fronha: int | None = None         # fronha_cortada - casados * fronha_mult


def _numero(valor) -> float | None:
    """Converte um valor de RegistroCorte.extra (sempre string, vem de
    input text) pra float — em branco ou não-numérico vira None, não 0
    (uma gramatura/metro em branco não pode zerar a média)."""
    if valor in (None, ""):
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        return None


def _media_ponderada(pares: list[tuple[float, int]]) -> float | None:
    """Média ponderada por `quantidade_pecas` — um registro de 500 peças
    pesa mais no cálculo do que um de 10, diferente da média simples entre
    valores lançados que existia antes. Confirmado pela planilha real, que
    já calcula assim — muda os números que o painel de Corte mostra hoje,
    não é capricho de precisão gratuita."""
    total_peso = sum(peso for _, peso in pares)
    if not pares or total_peso <= 0:
        return None
    return sum(valor * peso for valor, peso in pares) / total_peso


def _gramatura_de(registro) -> float | None:
    """Campo real primeiro; cai pro antigo `extra["gramatura"]` só pra
    registros lançados antes desta migração — sem backfill obrigatório,
    eles continuam lidos até alguém editar o registro (aí passa a gravar
    no campo novo)."""
    if registro.gramatura is not None:
        return float(registro.gramatura)
    return _numero(registro.extra.get("gramatura"))


def _kg_por_metro_medio(registros: list) -> float | None:
    # Simples, não ponderada: kg_por_metro é uma propriedade do TECIDO (kg
    # por metro linear do rolo), não uma grandeza por peça — não faz
    # sentido pesar pelo tanto de peças que cada registro cortou.
    valores = [v for r in registros if (v := _numero(r.extra.get("kg_por_metro"))) is not None]
    return sum(valores) / len(valores) if valores else None


def calcular_aproveitamento(programacao: ProgramacaoCorte) -> Aproveitamento:
    """Soma os RegistroCorte da OP e calcula previsto × realizado. Os
    campos específicos de Manta (kg_final/divergencia_kg/
    aproveitamento_peso_pct) e de Lençol (metros_programado/
    metros_cortado_real) só vêm preenchidos quando a unidade da OP é dessa
    família E os registros trazem os dados necessários (gramatura/
    metros_por_peca)."""
    registros = list(programacao.registros.all())
    pecas_realizado = sum(r.quantidade_pecas for r in registros)
    kg_cortado_total = (
        sum(float(r.kg_cortado) for r in registros if r.kg_cortado is not None)
        if any(r.kg_cortado is not None for r in registros) else None
    )
    retalho_kg_total = (
        sum(float(r.retalho_kg) for r in registros if r.retalho_kg is not None)
        if any(r.retalho_kg is not None for r in registros) else None
    )
    baby_kg_total = (
        sum(float(r.baby_kg) for r in registros if r.baby_kg is not None)
        if any(r.baby_kg is not None for r in registros) else None
    )
    plastico_kg_total = (
        sum(float(r.plastico_kg) for r in registros if r.plastico_kg is not None)
        if any(r.plastico_kg is not None for r in registros) else None
    )
    tubo_kg_total = (
        sum(float(r.tubo_kg) for r in registros if r.tubo_kg is not None)
        if any(r.tubo_kg is not None for r in registros) else None
    )

    pct_pecas = (
        pecas_realizado / programacao.qnt_programada
        if programacao.qnt_programada else None
    )

    retalho_pct = None
    if kg_cortado_total is not None:
        base_kg = float(kg_cortado_total) + float(retalho_kg_total or 0)
        if base_kg > 0:
            retalho_pct = float(retalho_kg_total or 0) / base_kg

    status_calculado = _status_corte(pecas_realizado, programacao.qnt_programada)

    resultado = Aproveitamento(
        saldo_pecas=max(programacao.qnt_programada - pecas_realizado, 0),
        cortado_pecas=pecas_realizado,
        pct_pecas=pct_pecas,
        retalho_pct=retalho_pct,
        status_calculado=status_calculado,
        kg_cortado_total=kg_cortado_total,
        retalho_kg_total=retalho_kg_total,
        baby_kg_total=baby_kg_total,
        plastico_kg_total=plastico_kg_total,
        tubo_kg_total=tubo_kg_total,
    )

    unidade = programacao.unidade_corte
    if unidade in UNIDADES_MANTA:
        _calcular_manta(resultado, registros, pecas_realizado, kg_cortado_total,
                        retalho_kg_total, baby_kg_total)
    elif unidade == UnidadeCorte.LENCOL:
        _calcular_lencol(resultado, registros, programacao, pecas_realizado)

    return resultado


def _calcular_manta(resultado, registros, pecas_realizado, kg_cortado_total,
                    retalho_kg_total, baby_kg_total):
    pares_gramatura = [(g, r.quantidade_pecas) for r in registros
                       if (g := _gramatura_de(r)) is not None]
    gramatura_media = _media_ponderada(pares_gramatura)
    resultado.gramatura_media = gramatura_media

    if gramatura_media is None or kg_cortado_total is None:
        return

    # Baby entra em kg direto agora (RegistroCorte.baby_kg, pesado igual ao
    # retalho) — sem o fator fixo de conversão peça→kg que o antigo
    # extra["babys_pecas"] precisava.
    kg_final = pecas_realizado * gramatura_media + float(retalho_kg_total or 0) + float(baby_kg_total or 0)
    resultado.kg_final = kg_final
    resultado.divergencia_kg = float(kg_cortado_total) - kg_final
    resultado.aproveitamento_peso_pct = kg_final / float(kg_cortado_total) if kg_cortado_total else None


def _calcular_lencol(resultado, registros, programacao, pecas_realizado):
    pares_metros = [(m, r.quantidade_pecas) for r in registros
                    if (m := _numero(r.extra.get("metros_por_peca"))) is not None]
    media_metros = _media_ponderada(pares_metros)
    resultado.metros_por_peca_medio = media_metros

    # "Programado" = metros efetivamente retirados do rolo pra trabalhar,
    # medido (RegistroCorte.metros_cortado) — não é o resultado final, é o
    # material separado/consumido do rolo (pode ter perda/emenda no meio,
    # por isso não é sempre igual à conta de peças×metro).
    metros_cortado_lancados = [float(r.metros_cortado) for r in registros if r.metros_cortado is not None]
    if metros_cortado_lancados:
        resultado.metros_programado = sum(metros_cortado_lancados)

    # Caseamento primeiro (se for jogo de cama) — precisa dos CASADOS antes
    # de calcular "Cortado Real" abaixo.
    eh_jogo, tamanho = eh_jogo_de_cama(programacao.categoria, programacao.produto)
    if eh_jogo:
        _calcular_caseamento(resultado, registros, pecas_realizado, tamanho)

    # "Cortado Real" = quantidade útil × metros por peça. Pra jogo de cama,
    # a quantidade útil é o CASADO (pares completos cima+fundo — um lençol
    # de cima sem fundo correspondente não é um jogo pronto); pras demais,
    # é a quantidade normal cortada.
    if media_metros is not None:
        quantidade_util = resultado.casados if (eh_jogo and resultado.casados is not None) else pecas_realizado
        resultado.metros_cortado_real = quantidade_util * media_metros

    if resultado.metros_programado is not None and resultado.metros_cortado_real is not None:
        resultado.divergencia_metros = resultado.metros_programado - resultado.metros_cortado_real
        if resultado.metros_programado > 0:
            resultado.aproveitamento_metros_pct = resultado.metros_cortado_real / resultado.metros_programado

    kg_por_metro = _kg_por_metro_medio(registros)
    if kg_por_metro and resultado.metros_programado is not None:
        resultado.kg_por_metro = kg_por_metro
        resultado.kg_convertidos = resultado.metros_programado * kg_por_metro


def _calcular_caseamento(resultado, registros, cima_cortado, tamanho):
    """Caseamento Jogo × Fundo × Fronha dentro de uma única OP — cima,
    fundo e fronha são lançados independentes na Gestão de Corte (fronha
    não é calculada, é o que o cortador informou de verdade). CASADOS é o
    quanto dá pra montar de jogo completo, limitado pelo mais escasso dos
    três já lançados (fronha entra dividida por fronha_mult — um caseado
    duplo consome 2 fronhas, simples consome 1). O resto de cada um vira
    avulso."""
    resultado.eh_jogo_de_cama = True
    mult = fronha_mult(tamanho)

    fundo_lancado = [f for r in registros if (f := _numero(r.extra.get("fundo_cortado"))) is not None]
    fundo_total = int(sum(fundo_lancado)) if fundo_lancado else None
    resultado.fundo_cortado = fundo_total

    fronha_lancada = [f for r in registros if (f := _numero(r.extra.get("fronha_cortado"))) is not None]
    fronha_total = int(sum(fronha_lancada)) if fronha_lancada else None
    resultado.fronha_cortada = fronha_total

    if fundo_total is None and fronha_total is None:
        return  # só cima lançado até agora — nada pra casear ainda

    candidatos = [cima_cortado]
    if fundo_total is not None:
        candidatos.append(fundo_total)
    if fronha_total is not None:
        candidatos.append(fronha_total // mult)

    casados = min(candidatos)
    resultado.casados = casados
    resultado.avulsos_cima = cima_cortado - casados
    if fundo_total is not None:
        resultado.avulsos_fundo = fundo_total - casados
    if fronha_total is not None:
        resultado.avulsos_fronha = fronha_total - casados * mult


def _status_corte(pecas_realizado: int, qnt_programada: int) -> str:
    if pecas_realizado <= 0:
        return ProgramacaoCorte.Status.PENDENTE
    eficiencia = pecas_realizado / qnt_programada if qnt_programada > 0 else 0
    if eficiencia >= LIMIAR_CONCLUIDO:
        return ProgramacaoCorte.Status.CONCLUIDO
    return ProgramacaoCorte.Status.PARCIAL


def atualizar_status_programacao(programacao: ProgramacaoCorte) -> ProgramacaoCorte:
    """Chamada após salvar um RegistroCorte novo — recalcula e persiste o
    status da OP, marcando data_finalizado na primeira vez que cruzar o
    limiar de Concluído."""
    resultado = calcular_aproveitamento(programacao)
    programacao.status = resultado.status_calculado
    if resultado.status_calculado == ProgramacaoCorte.Status.CONCLUIDO and not programacao.data_finalizado:
        programacao.data_finalizado = date.today()
    programacao.save(update_fields=["status", "data_finalizado", "atualizado_em"])
    return programacao
