"""Helpers de querystring compartilhados entre os dashboards (Produção, Corte
etc.) — cada um tem o mesmo seletor de período (mês de referência OU dia
único/intervalo customizado)."""
from datetime import datetime


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
