from django.urls import NoReverseMatch, reverse
from django.utils.text import slugify

from contas.permissions import modulos_liberados

from .modulos import ABA_ICONES, ABA_LABEL_CURTO, MODULOS, modulos_por_aba


def _href_modulo(m):
    """(href, externa) do módulo, ou None se a URL não resolver (módulo
    órfão) — usado tanto no índice de busca (nav_search) quanto no atalho de
    abertura direta de aba com um módulo só (ver `link_direto` abaixo)."""
    if m.get("url_externa"):
        return m["url_externa"], True
    try:
        href = reverse(m["url_name"]) if m.get("url_name") else reverse("paineis:modulo", args=[m["slug"]])
    except NoReverseMatch:
        return None
    return href, False


def navegacao(request):
    """Injeta os módulos (agrupados por setor, já filtrados pelo setor do
    usuário — ver contas/permissions.py::modulos_liberados), a aba ativa e
    um índice achatado (nav_search) em todos os templates.

    A sidebar (ver templates/base.html) é um rail de ícones: `nav_setores`
    já vem pronto pro template iterar sem precisar de slugify/dict-lookup
    (Django template não faz lookup de dict por chave com espaço/acento) —
    cada item carrega ícone, rótulo curto e slug prontos, na ordem de
    `modulos.ABAS` (GUT entra ali como mais um setor, hoje entre Ferramentas
    e Planilhas — sem tratamento especial). Uma aba com um único módulo
    carrega `link_direto` (href pronto) — o rail abre a página direto nesse
    caso, sem passar pelo painel flutuante (ver `mods|length == 1` em
    base.html). A busca global (Ctrl+K) continua usando nav_search, que
    filtra todos os módulos de uma vez sem duplicar a lista em Python/JS."""
    modulos_visiveis = modulos_liberados(request.user, MODULOS)

    aba_ativa = ""
    rm = getattr(request, "resolver_match", None)
    if rm is not None:
        view_name = rm.view_name           # ex.: "producao:dashboard"
        slug = rm.kwargs.get("slug")        # ex.: placeholder de módulo
        for m in modulos_visiveis:
            if (m.get("url_name") and m["url_name"] == view_name) or (slug and m["slug"] == slug):
                aba_ativa = m["aba"]
                break

    nav_search = []
    for m in modulos_visiveis:
        resolvido = _href_modulo(m)
        if resolvido is None:
            continue
        href, externa = resolvido
        nav_search.append({
            "nome": m["nome"], "subtitulo": m.get("subtitulo", ""), "aba": m["aba"],
            "href": href, "externa": externa, "admin_only": bool(m.get("admin_only")),
        })

    def _link_direto(mods):
        """Só quando a aba tem exatamente 1 módulo e ele não está trancado
        pra este usuário — senão o rail precisa continuar abrindo o painel
        (pra mostrar o cadeado, ou pra escolher entre vários módulos)."""
        if len(mods) != 1:
            return None
        modulo = mods[0]
        if modulo.get("admin_only") and not request.user.is_superuser:
            return None
        resolvido = _href_modulo(modulo)
        if resolvido is None:
            return None
        href, externa = resolvido
        return {"href": href, "externa": externa}

    nav_setores = [
        {
            "nome": aba, "slug": slugify(aba), "label_curto": ABA_LABEL_CURTO.get(aba, aba),
            "icone": ABA_ICONES.get(aba, ""), "mods": mods, "ativa": aba == aba_ativa,
            "link_direto": _link_direto(mods),
        }
        for aba, mods in modulos_por_aba(modulos_visiveis).items() if mods
    ]

    return {"aba_ativa": aba_ativa, "nav_search": nav_search, "nav_setores": nav_setores}
