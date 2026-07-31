from django.urls import NoReverseMatch, reverse

from .modulos import MODULOS, modulos_por_aba


def navegacao(request):
    """Injeta os módulos (agrupados por aba), a aba ativa e um índice achatado
    (nav_search) em todos os templates — o menu lateral usa nav_abas pra abrir
    só o setor da página atual, a busca global (Ctrl+K) usa nav_search pra
    filtrar todos os módulos de uma vez, sem duplicar a lista em Python/JS."""
    aba_ativa = ""
    rm = getattr(request, "resolver_match", None)
    if rm is not None:
        view_name = rm.view_name           # ex.: "producao:dashboard"
        slug = rm.kwargs.get("slug")        # ex.: placeholder de módulo
        for m in MODULOS:
            if (m.get("url_name") and m["url_name"] == view_name) or (slug and m["slug"] == slug):
                aba_ativa = m["aba"]
                break

    nav_search = []
    for m in MODULOS:
        if m.get("url_externa"):
            href, externa = m["url_externa"], True
        else:
            try:
                href = reverse(m["url_name"]) if m.get("url_name") else reverse("paineis:modulo", args=[m["slug"]])
            except NoReverseMatch:
                continue
            externa = False
        nav_search.append({
            "nome": m["nome"], "subtitulo": m.get("subtitulo", ""), "aba": m["aba"],
            "href": href, "externa": externa, "admin_only": bool(m.get("admin_only")),
        })

    return {"nav_abas": modulos_por_aba(), "aba_ativa": aba_ativa, "nav_search": nav_search}
