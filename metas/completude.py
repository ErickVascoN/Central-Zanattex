"""Checagem de completude da Produção Diária (facções externas) para o envio
automático diário (D-1).

Escopo desta 1ª versão: só facções externas — prestadores internos (LITTEX,
GGTTEX Jogos/Fronha/Cortina) ficam de fora por enquanto, tanto do relatório
quanto desta checagem.
"""

from __future__ import annotations

from datetime import date

from producao.servicos import carregar_producao, lancamento_efetivo
from . import servicos
from .matching import LITTEX_SENTINEL


def prestadores_faltando(data_ref: date) -> list[str]:
    """Prestadores externos previstos no Plano de Metas do mês de `data_ref`
    que ainda não têm produção lançada em `data_ref`. Lista vazia se não há
    plano cadastrado pro mês (nada a checar)."""
    return situacao(data_ref)["faltando"]


def situacao(data_ref: date) -> dict:
    """Quem lançou e quem não lançou em `data_ref`, entre os prestadores
    externos previstos no Plano de Metas do mês.

    O e-mail diário mostra os dois lados: só a lista de ausentes não diz se
    "faltam 10" é de 12 prestadores ou de 40 — sem o denominador não dá pra
    saber se o dia está quase fechado ou mal começou."""
    vazio = {"presentes": [], "faltando": []}
    df_bruto = servicos.carregar_plano_metas_bruto()
    if df_bruto.empty:
        return vazio

    mes_alvo = next(
        (m for m in servicos.meses_disponiveis(df_bruto)
         if m.year == data_ref.year and m.month == data_ref.month),
        None,
    )
    if mes_alvo is None:
        return vazio

    cruzado = servicos.cruzar_mes(df_bruto, mes_alvo)
    acabado = cruzado["acabado"]
    if acabado.empty:
        return vazio

    df_prod = carregar_producao()
    faccoes_externas_conhecidas = set(df_prod["FACCAO"].unique()) if not df_prod.empty else set()

    esperados = {
        r for r in acabado["RESPONSAVEL_RESOLVIDO"].dropna().unique()
        if r != LITTEX_SENTINEL and r in faccoes_externas_conhecidas
    }
    if not esperados:
        return vazio

    presentes_no_dia = set()
    if not df_prod.empty:
        do_dia = df_prod[df_prod["DATA"].dt.date == data_ref]
        # Só conta como lançado o que é dado de fato: produção (>0) ou zero
        # CONFIRMADO. Uma linha zerada só pra registrar "o prestador ainda não
        # enviou" é a própria pendência — se contasse aqui, o e-mail parava de
        # cobrar justamente quem falta (ver servicos.lancamento_efetivo).
        do_dia = do_dia[lancamento_efetivo(do_dia)]
        presentes_no_dia = set(do_dia["FACCAO"].unique())

    return {
        "presentes": sorted(esperados & presentes_no_dia),
        "faltando": sorted(esperados - presentes_no_dia),
    }


def quem_lancou(data_ref: date) -> list[str]:
    """Facções com produção lançada em `data_ref`, SEM filtrar pelo Plano de
    Metas. `situacao()` só olha quem o plano esperava, o que é o certo pra
    cobrar pendência num dia útil — mas num sábado não há plano a cobrar, e
    quem trabalhou costuma estar fora dele. Filtrar ali fazia o bloco do
    sábado aparecer sem nomear ninguém."""
    df_prod = carregar_producao()
    if df_prod.empty:
        return []
    do_dia = df_prod[df_prod["DATA"].dt.date == data_ref]
    do_dia = do_dia[lancamento_efetivo(do_dia)]
    return sorted(do_dia["FACCAO"].dropna().unique())


def houve_producao(data_ref: date) -> bool:
    """Alguma facção lançou produção em `data_ref`. Diferente de
    `prestadores_faltando`, que cobra quem o Plano de Metas esperava: aqui a
    pergunta é se o dia existiu — usado pra decidir se um sábado/feriado entra
    no e-mail (ver `relatorios/cron.py`), já que nesses dias não há
    lançamento esperado e a lista de faltantes não diz nada."""
    df_prod = carregar_producao()
    if df_prod.empty:
        return False
    do_dia = df_prod[df_prod["DATA"].dt.date == data_ref]
    # Um sábado em que só existe linha de "aguardando envio" não é um sábado
    # que aconteceu — é uma pendência anotada. Sem isso o e-mail abriria o
    # bloco do sábado sem nada de concreto pra mostrar.
    return bool(lancamento_efetivo(do_dia).any())
