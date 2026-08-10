"""Helpers de querystring compartilhados entre os dashboards (Produção, Corte
etc.) — cada um tem o mesmo seletor de período (mês de referência OU dia
único/intervalo customizado)."""
from datetime import date, datetime, timedelta

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


def janela_comparacao(data_de: date, data_ate: date, modo: str):
    """Janela de comparação, SEMPRE do mesmo tamanho da janela atual.

    Comparar tamanhos diferentes é o erro clássico aqui: no dia 8, "o mês
    atual" tem 8 dias de dado e o mês anterior tem 31 — a conta acusaria uma
    queda de ~74% que não existe. Por isso quem chama deve passar a janela
    EFETIVA (primeiro e último dia com dado no período), não o mês nominal.

    - "anterior":    os N dias imediatamente antes de `data_de`.
    - "ano_passado": a mesma janela um ano atrás.

    Retorna (None, None) com o comparativo desligado.
    """
    if modo not in ("anterior", "ano_passado") or data_de is None or data_ate is None:
        return None, None
    if modo == "ano_passado":
        return _recuar_um_ano(data_de), _recuar_um_ano(data_ate)
    fim = data_de - timedelta(days=1)
    return fim - timedelta(days=(data_ate - data_de).days), fim


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
