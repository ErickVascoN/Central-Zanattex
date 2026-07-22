from .modulos import modulos_por_aba


def navegacao(request):
    """Injeta os módulos (agrupados por aba) em todos os templates, para o menu lateral."""
    return {"nav_abas": modulos_por_aba()}
