"""
Registro central dos módulos da Central de Dados Zanattex.

Espelha os setores/dashboards que existem hoje na Central (Streamlit), agrupados
nas duas abas da home: "Análise de Dados" e "Controladoria", mais o grupo
"Logística" com a Calculadora de Frete.

Nesta Fase 1 os módulos são placeholders navegáveis — cada um vira uma tela
própria nas fases seguintes, portando o dashboard correspondente. O campo
`origem` documenta de onde o conteúdo será portado.
"""

# aba -> lista de módulos
MODULOS = [
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
        "icone": "🏭",
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
        "icone": "✂️",
        "tags": ["Operação", "Corte", "Metas diárias"],
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
        "icone": "🚛",
        "tags": ["Logística", "Cargas", "Previsão vs. Realizado"],
        "origem": "pages/8_Previsao_Cargas.py",
    },
    {
        "slug": "metas",
        "aba": "Análise de Dados",
        "nome": "Metas / Previsão de Custos",
        "subtitulo": "Progresso automático vs. metas e projeção",
        "descricao": (
            "Metas por prestador e unidade, com projeção de atingimento e custos."
        ),
        "icone": "🎯",
        "tags": ["Metas", "Previsão", "Custos"],
        "origem": "pages/7_Plano_de_Metas.py",
        "admin_only": True,
    },
    # ---------------- Controladoria ----------------
    {
        "slug": "programacao",
        "aba": "Controladoria",
        "nome": "Programação de Corte",
        "subtitulo": "Planejado vs. Realizado",
        "descricao": (
            "Cruza a programação semanal de corte com o que foi realmente cortado. "
            "Status por OP: Pendente, Parcial e Concluído em tempo real."
        ),
        "icone": "📋",
        "tags": ["Programação", "Corte", "Planejado vs Realizado"],
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
        "icone": "🗂️",
        "tags": ["OP", "Fechamento", "Histórico"],
        "origem": "pages/11_Controle_de_OP.py",
    },
    {
        "slug": "carteira",
        "aba": "Controladoria",
        "nome": "Carteira de Pedidos",
        "subtitulo": "Comercial — pedidos em aberto",
        "descricao": (
            "Visão consolidada da carteira: análise por cliente, categoria, tamanho "
            "e região, com KPIs, gráficos e evolução mensal."
        ),
        "icone": "📦",
        "tags": ["Pedidos", "Comercial", "Análise"],
        "origem": "pages/9_Carteira_de_Pedidos.py",
    },
    {
        "slug": "relatorios",
        "aba": "Controladoria",
        "nome": "Relatórios",
        "subtitulo": "Central de geração de PDFs",
        "descricao": (
            "Gera relatórios PDF de todos os módulos em um só lugar: Corte, Produção, "
            "Facções, Previsão de Cargas, Carteira e Programação."
        ),
        "icone": "📄",
        "tags": ["PDF", "Relatórios", "Exportar"],
        "url_name": "relatorios:hub",  # hub já implementado (Produção)
        "origem": "pages/10_Relatorios.py",
    },
    {
        "slug": "historico",
        "aba": "Controladoria",
        "nome": "Histórico de Dados",
        "subtitulo": "Backup do banco — todas as fontes",
        "descricao": (
            "Backup permanente de toda produção, corte e cargas. Protege contra perda "
            "ou alteração das planilhas. Consulta por período, export CSV e status."
        ),
        "icone": "🗄️",
        "tags": ["Backup", "Histórico"],
        "origem": "pages/6_Historico.py",
        "admin_only": True,
    },
    # ---------------- Logística ----------------
    {
        "slug": "frete",
        "aba": "Logística",
        "nome": "Calculadora de Frete",
        "subtitulo": "Formação de preço + análise",
        "descricao": (
            "Simulador de formação de preço de frete e central de análise (totais por "
            "mês, cliente e indicador). Portado da calculadora atual."
        ),
        "icone": "🧮",
        "tags": ["Frete", "Custos", "Logística"],
        "url_name": "frete:index",  # Fase 1: calculadora hospedada no app (iframe)
        "origem": "Calculadora de Frete (HTML + Supabase)",
    },
]

# Ordem das abas na home
ABAS = ["Análise de Dados", "Controladoria", "Logística"]

MODULOS_POR_SLUG = {m["slug"]: m for m in MODULOS}


def modulos_por_aba():
    """Retorna {aba: [modulos]} preservando a ordem de ABAS."""
    agrupado = {aba: [] for aba in ABAS}
    for m in MODULOS:
        agrupado.setdefault(m["aba"], []).append(m)
    return agrupado
