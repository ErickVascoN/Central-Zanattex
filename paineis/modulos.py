"""
Registro central dos módulos da Central de Dados Zanattex.

Espelha os setores/dashboards que existem hoje na Central (Streamlit), agrupados
em três seções na sidebar, nesta ordem: "Controladoria", "Análise de Dados" e
"Relatórios" (seção própria — agrega dados de todos os outros módulos, não é só
Controladoria nem só Análise). A Calculadora de Frete mora em Controladoria,
não vale uma seção própria pra um item só.

Nesta Fase 1 os módulos são placeholders navegáveis — cada um vira uma tela
própria nas fases seguintes, portando o dashboard correspondente. O campo
`origem` documenta de onde o conteúdo será portado.

O campo `icone` guarda o markup de um ícone SVG (24x24, stroke=currentColor)
renderizado com `|safe` no template — ver templates/base.html e
templates/paineis/modulo.html.
"""

# aba -> lista de módulos
MODULOS = [
    # ---------------- Controladoria ----------------
    {
        "slug": "carteira",
        "aba": "Controladoria",
        "nome": "Carteira de Pedidos",
        "subtitulo": "Comercial — pedidos em aberto",
        "descricao": (
            "Visão consolidada da carteira: análise por cliente, categoria, tamanho "
            "e região, com KPIs, gráficos e evolução mensal."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3.5 8.5l8.5 5 8.5-5"/><line x1="12" y1="13.5" x2="12" y2="21.5"/></svg>',
        "tags": ["Pedidos", "Comercial", "Análise"],
        "url_name": "carteira:dashboard",
        "origem": "pages/9_Carteira_de_Pedidos.py",
        "admin_only": True,
    },
    {
        "slug": "programacao",
        "aba": "Controladoria",
        "nome": "Programação de Corte",
        "subtitulo": "Planejado vs. Realizado",
        "descricao": (
            "Cruza a programação semanal de corte com o que foi realmente cortado. "
            "Status por OP: Pendente, Parcial e Concluído em tempo real."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2h6a1 1 0 0 1 1 1v2H8V3a1 1 0 0 1 1-1z"/><rect x="5" y="4" width="14" height="18" rx="2"/><path d="M9 12l2 2 4-4"/></svg>',
        "tags": ["Programação", "Corte", "Planejado vs Realizado"],
        "url_name": "programacao:dashboard",
        "origem": "pages/4_Controladoria_Programacao.py",
    },
    {
        "slug": "ops",
        "aba": "Controladoria",
        "nome": "Controle de OP",
        "subtitulo": "Histórico e Fechamento",
        "descricao": (
            "Status, % de conclusão e histórico de fechamento de OPs, calculados a "
            "partir da programação e do corte real. Relatório de fechamento em PDF."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
        "tags": ["OP", "Fechamento", "Histórico"],
        "origem": "pages/11_Controle_de_OP.py",
    },
    {
        "slug": "frete",
        "aba": "Controladoria",
        "nome": "Calculadora de Frete",
        "subtitulo": "Formação de preço + análise",
        "descricao": (
            "Simulador de formação de preço de frete e central de análise (totais por "
            "mês, cliente e indicador). Portado da calculadora atual."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><line x1="8" y1="10.5" x2="8" y2="10.5"/><line x1="12" y1="10.5" x2="12" y2="10.5"/><line x1="16" y1="10.5" x2="16" y2="10.5"/><line x1="8" y1="14.5" x2="8" y2="14.5"/><line x1="12" y1="14.5" x2="12" y2="14.5"/><line x1="16" y1="14.5" x2="16" y2="14.5"/><line x1="8" y1="18.5" x2="8" y2="18.5"/><line x1="12" y1="18.5" x2="12" y2="18.5"/><line x1="16" y1="18.5" x2="16" y2="18.5"/></svg>',
        "tags": ["Frete", "Custos", "Logística"],
        "url_name": "frete:index",  # Fase 1: calculadora hospedada no app (iframe)
        "origem": "Calculadora de Frete (HTML + Supabase)",
    },
    # ---------------- Análise de Dados ----------------
    {
        "slug": "producao",
        "aba": "Análise de Dados",
        "nome": "Análise de Produção",
        "subtitulo": "Por Cliente e Por Colaborador",
        "descricao": (
            "Acompanhamento da produção em duas visões: Por Cliente (multi-empresa, "
            "metas e evolução) e Por Colaborador (LITTEX e GGTTEX — ranking, "
            "consistência e externos)."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
        "tags": ["Produção", "Por Cliente", "Por Colaborador"],
        "origem": "pages/2_Producao_Geral.py + 5_Producao_Faccoes.py",
        "url_name": "producao:dashboard",  # módulo já implementado (Fase 3)
    },
    {
        "slug": "corte",
        "aba": "Análise de Dados",
        "nome": "Análise de Corte",
        "subtitulo": "Mantas / Lençol — estações e desempenho",
        "descricao": (
            "Painel operacional dos setores de corte: metas diárias por estação, "
            "produção, OPs, cores, indicadores por operador e ranking de desempenho."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/></svg>',
        "tags": ["Operação", "Corte", "Metas diárias"],
        "url_name": "corte:dashboard",  # em migração (Fase 3) — Visão Geral portada
        "origem": "pages/3_Controle_de_Corte.py",
    },
    {
        "slug": "cargas",
        "aba": "Análise de Dados",
        "nome": "Previsão de Cargas",
        "subtitulo": "Logística · Previsão vs. Realizado",
        "descricao": (
            "Comparativo mensal previsão vs. realizado, aderência por destino, "
            "análise por origem e timeline de cargas."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>',
        "tags": ["Logística", "Cargas", "Previsão vs. Realizado"],
        "url_name": "cargas:dashboard",
        "origem": "pages/8_Previsao_Cargas.py",
        "admin_only": True,
    },
    {
        "slug": "metas",
        "aba": "Análise de Dados",
        "nome": "Plano de Metas",
        "subtitulo": "Previsto x Realizado — Cliente, Prestador e Produto",
        "descricao": (
            "Previsto x Realizado automático (calculado a partir da produção real, "
            "não do lançamento manual) por Cliente, Prestador e Produto, com "
            "ranking de desvios e divergências pra revisão."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
        "tags": ["Metas", "Previsto x Realizado"],
        "url_name": "metas:dashboard",
        "origem": "pages/7_Plano_de_Metas.py",
        "admin_only": True,
    },
    # ---------------- Relatórios ----------------
    {
        "slug": "relatorios",
        "aba": "Relatórios",
        "nome": "Relatórios",
        "subtitulo": "Central de geração de PDFs",
        "descricao": (
            "Gera relatórios PDF de todos os módulos em um só lugar: Corte, Produção, "
            "Facções, Previsão de Cargas, Carteira e Programação."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>',
        "tags": ["PDF", "Relatórios", "Exportar"],
        "url_name": "relatorios:hub",  # hub já implementado (Produção)
        "origem": "pages/10_Relatorios.py",
    },
    # ---------------- GUT (AppSheet — apontamento individual) ----------------
    # Links externos: não têm url_name/slug próprio, abrem o AppSheet direto
    # numa aba nova (ver `url_externa` em templates/base.html).
    {
        "slug": "gut-zanattex",
        "aba": "GUT",
        "nome": "GUT Zanattex",
        "subtitulo": "GIATTEX / GGTTEX / LITTEX",
        "descricao": "Apontamento individual de produção (AppSheet) — GIATTEX, GGTTEX e LITTEX.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["GUT", "AppSheet", "Apontamento"],
        "url_externa": "https://www.appsheet.com/start/bcbb42c8-42a6-4424-8cb2-c11a69052d89#appName=ApontadorZanattex20-819603934&group=%5B%7B\"Column\"%3A\"Data\"%2C\"Order\"%3A\"Descending\"%7D%5D&page=fastTable&sort=%5B%7B\"Column\"%3A\"Hora\"%2C\"Order\"%3A\"Descending\"%7D%2C%7B\"Column\"%3A\"Eficiência\"%2C\"Order\"%3A\"Descending\"%7D%5D&table=GIATTEX&view=GIATTEX",
        "origem": "AppSheet (ApontadorZanattex20)",
    },
    {
        "slug": "gut-mega-previttex",
        "aba": "GUT",
        "nome": "GUT Mega/Previttex",
        "subtitulo": "Mega Preven Matriz/002/003 / Previttex Matriz",
        "descricao": "Apontamento individual de produção (AppSheet) — Mega Preven Matriz, 002, 003 e Previttex Matriz.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["GUT", "AppSheet", "Apontamento"],
        "url_externa": "https://www.appsheet.com/start/a1152114-53c8-413b-8501-588883b332a4#appName=ApontadorMegaprevenPrevitex-819603934-26-06-30&group=%5B%7B\"Column\"%3A\"Data\"%2C\"Order\"%3A\"Descending\"%7D%5D&page=fastTable&sort=%5B%7B\"Column\"%3A\"Hora\"%2C\"Order\"%3A\"Descending\"%7D%2C%7B\"Column\"%3A\"Eficiência\"%2C\"Order\"%3A\"Descending\"%7D%5D&table=MEGAPREVEN+MATRIZ&view=MEGAPREVEN+MATRIZ",
        "origem": "AppSheet (ApontadorMegaprevenPrevitex)",
    },
]

# Ordem das abas na sidebar
ABAS = ["Controladoria", "Análise de Dados", "Relatórios", "GUT"]

MODULOS_POR_SLUG = {m["slug"]: m for m in MODULOS}


def modulos_por_aba():
    """Retorna {aba: [modulos]} preservando a ordem de ABAS."""
    agrupado = {aba: [] for aba in ABAS}
    for m in MODULOS:
        agrupado.setdefault(m["aba"], []).append(m)
    return agrupado
