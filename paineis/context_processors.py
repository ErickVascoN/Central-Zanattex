from .modulos import MODULOS, modulos_por_aba


def navegacao(request):
    """Injeta os módulos (agrupados por aba) e a aba ativa em todos os templates,
    para o menu lateral retrátil abrir só o setor da página atual."""
    aba_ativa = ""
    rm = getattr(request, "resolver_match", None)
    if rm is not None:
        view_name = rm.view_name           # ex.: "producao:dashboard"
        slug = rm.kwargs.get("slug")        # ex.: placeholder de módulo
        for m in MODULOS:
            if (m.get("url_name") and m["url_name"] == view_name) or (slug and m["slug"] == slug):
                aba_ativa = m["aba"]
                break

    return {"nav_abas": modulos_por_aba(), "aba_ativa": aba_ativa}
