"""Helpers de querystring compartilhados entre os dashboards (Produção, Corte
etc.) — cada um tem o mesmo seletor de período (mês de referência OU dia
único/intervalo customizado)."""
from datetime import date, datetime, timedelta

from .feriados import contar_dias_uteis, eh_dia_util

MODOS_COMPARACAO = ("", "anterior", "ano_passado")


def periodo_custom_de_request(request):
    """Lê 'de'/'ate' da querystring (dia único ou intervalo). Retorna
    (data_de, data_ate) como date ou (None, None) se ausente/inválido."""
    de_raw = request.GET.get("de")
    if not de_raw:
        return None, None
    try:
        data_de = datetime.strptime(de_raw, "%Y-%m-%d").date()
        ate_raw = request.GET.get("ate")
        data_ate = datetime.strptime(ate_raw, "%Y-%m-%d").date() if ate_raw else data_de
        if data_ate < data_de:
            data_de, data_ate = data_ate, data_de
        return data_de, data_ate
    except ValueError:
        return None, None


def modo_comparacao_de_request(request) -> str:
    """Lê 'cmp' da querystring. "" = comparativo desligado."""
    modo = (request.GET.get("cmp") or "").strip()
    return modo if modo in MODOS_COMPARACAO else ""


def _recuar_um_ano(d: date) -> date:
    """Mesma data no ano anterior. 29/02 vira 28/02 — `replace(year=...)`
    estoura em ano não-bissexto e derrubaria o dashboard inteiro."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _dia_util_ate(d: date) -> date:
    """O próprio dia, se for útil; senão o dia útil anterior mais próximo."""
    while not eh_dia_util(d):
        d -= timedelta(days=1)
    return d


def _inicio_com_n_uteis(fim: date, n: int) -> date:
    """Início da janela que termina em `fim` e contém exatamente `n` dias
    úteis. `fim` precisa ser dia útil."""
    d, vistos = fim, 0
    while True:
        if eh_dia_util(d):
            vistos += 1
            if vistos == n:
                return d
        d -= timedelta(days=1)


def janela_comparacao(data_de: date, data_ate: date, modo: str):
    """Janela de comparação com o MESMO NÚMERO DE DIAS ÚTEIS da janela atual.

    Comparar janelas de tamanhos diferentes é o erro clássico aqui: no dia 8,
    "o mês atual" tem 8 dias de dado e o mês anterior tem 31 — a conta acusaria
    uma queda de ~74% que não existe. Por isso quem chama deve passar a janela
    EFETIVA (primeiro e último dia com dado no período), não o mês nominal.

    O tamanho é medido em DIAS ÚTEIS, não corridos, porque é isso que a análise
    compara: os dois lados são filtrados a dias úteis antes de somar (sábado é
    turno curto e distorceria). Casar só os dias corridos não bastava — as duas
    pontas caíam com quantidades diferentes de dias úteis em ~70% das janelas
    ("anterior") e ~51% ("ano passado"), e aí o total comparava, por exemplo,
    3 dias de trabalho contra 5. Uma semana seg→sex era o pior caso: a janela
    anterior caía em cima de um fim de semana. Só janelas múltiplas de 7 dias
    escapavam, por acaso.

    - "anterior":    os N dias úteis imediatamente antes de `data_de`.
    - "ano_passado": N dias úteis terminando na data equivalente do ano
      anterior. Não são exatamente as mesmas datas: o dia da semana anda 1–2
      dias por ano e os feriados móveis mudam de lugar, então manter o
      calendário idêntico é justamente o que desalinha o volume de trabalho.

    Feriado nacional/SP conta como não-útil dos dois lados (integracao/feriados).

    Retorna (None, None) com o comparativo desligado ou quando a janela atual
    não tem nenhum dia útil (ex.: um sábado isolado) — aí não há o que comparar.
    """
    if modo not in ("anterior", "ano_passado") or data_de is None or data_ate is None:
        return None, None
    n = contar_dias_uteis(data_de, data_ate)
    if n == 0:
        return None, None
    fim = _dia_util_ate(_recuar_um_ano(data_ate) if modo == "ano_passado"
                        else data_de - timedelta(days=1))
    return _inicio_com_n_uteis(fim, n), fim


def variacao(atual: float, anterior: float) -> dict | None:
    """Selo de variação de um KPI. `None` quando não há base de comparação
    (janela anterior sem dado) — melhor não mostrar nada do que mostrar
    "+100%" contra um zero que só significa "não sei"."""
    if not anterior:
        return None
    delta = atual - anterior
    return {
        "delta": delta,
        "pct": round(delta / anterior * 100, 1),
        "direcao": "up" if delta > 0 else "down" if delta < 0 else "flat",
        # Os dois lados da conta, porque o selo os exibe: o KPI acima dele pode
        # estar noutra base (o card conta o sábado, a comparação não), e sem
        # mostrar os valores a porcentagem parece não fechar com o número
        # exibido — ver `_partials/selo_variacao.html`.
        "anterior": anterior,
        "atual": atual,
    }
