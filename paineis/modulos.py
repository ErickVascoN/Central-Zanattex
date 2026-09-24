"""
Registro central dos módulos da Central de Dados Zanattex.

Espelha os setores/dashboards que existem hoje na Central (Streamlit), agrupados
em seções na sidebar, nesta ordem: "Controladoria", "Análise de Dados",
"Relatórios" (seção própria — agrega dados de todos os outros módulos, não é só
Controladoria nem só Análise), "Apps" (ferramentas/apps internos que não são
dashboards de dados — hoje só a Calculadora de Frete, mas é aqui que qualquer
app novo que criarmos deve entrar), "GUT" e "Planilhas".

Nesta Fase 1 os módulos são placeholders navegáveis — cada um vira uma tela
própria nas fases seguintes, portando o dashboard correspondente. O campo
`origem` documenta de onde o conteúdo será portado.

O campo `icone` guarda o markup de um ícone SVG (24x24, stroke=currentColor)
renderizado com `|safe` no template — ver templates/base.html e
templates/paineis/modulo.html.
"""
from django.conf import settings
from django.urls import reverse_lazy

# aba -> lista de módulos
MODULOS = [
    # ---------------- Controladoria (gestão/entrada de dado) ----------------
    {
        "slug": "nova-programacao",
        "aba": "Controladoria",
        "nome": "Programação de Corte",
        "subtitulo": "Carteira → Programação de Corte",
        "descricao": (
            "Escolhe um pedido em aberto na Carteira e programa o corte da semana — "
            "entrada real do fluxo, substitui o lançamento manual na planilha."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
        "tags": ["Programação", "Corte", "Carteira"],
        "url_name": "programacao:nova_programacao",
        "origem": "Fluxo novo — sem equivalente no Streamlit original.",
        "setores": ["PCP"],
    },
    {
        # Tela única do ciclo da OP — absorveu a antiga "Gestão de Corte"
        # (que era uma entrada separada só com a fila + o formulário de
        # corte). É a mesma OP, então virou o mesmo lugar: quem é do Corte
        # vê a fila da sua unidade e lança o corte; PCP/Controladoria vê a
        # OP inteira (envio, retorno, produção diária e os 3 fechamentos).
        "slug": "ops",
        "aba": "Controladoria",
        "nome": "Gestão de OP",
        "subtitulo": "Corte · Produção · Faturamento",
        "descricao": (
            "Ciclo completo da OP num lugar só: registro do corte, envio pra "
            "industrialização, retorno de peças e retalho, produção diária puxada da "
            "planilha de facções e os 3 fechamentos. Relatório de fechamento em PDF."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
        "tags": ["OP", "Corte", "Produção", "Fechamento"],
        "url_name": "controle_op:lista",
        "origem": "pages/11_Controle_de_OP.py + fluxo novo de Gestão de Corte.",
        "setores": ["CORTE", "PCP", "CONTROLADORIA"],
    },
    # ---------------- Análise de Dados (só leitura) ----------------
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
        "setores": ["PCP", "RH"],
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
        "setores": ["PCP", "RH"],
    },
    {
        "slug": "cargas",
        "aba": "Análise de Dados",
        "nome": "Cargas - Previsto x Realizado",
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
        "setores": ["PCP"],
    },
    {
        "slug": "carteira",
        "aba": "Análise de Dados",
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
        "setores": ["PCP"],
    },
    {
        "slug": "programacao",
        "aba": "Análise de Dados",
        "nome": "Análise de Programação de Corte",
        "subtitulo": "Planejado vs. Realizado",
        "descricao": (
            "Cruza a programação semanal de corte com o que foi realmente cortado. "
            "Status por OP: Pendente, Parcial e Concluído em tempo real."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2h6a1 1 0 0 1 1 1v2H8V3a1 1 0 0 1 1-1z"/><rect x="5" y="4" width="14" height="18" rx="2"/><path d="M9 12l2 2 4-4"/></svg>',
        "tags": ["Programação", "Corte", "Planejado vs Realizado"],
        "url_name": "programacao:dashboard",
        "origem": "pages/4_Controladoria_Programacao.py",
        "setores": ["PCP"],
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
        "setores": ["PCP"],
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
        "setores": ["PCP", "RH"],
    },
    # ---------------- Apps (ferramentas/apps internos, fora dos dashboards) ----------------
    {
        "slug": "frete",
        "aba": "Ferramentas",
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
        "setores": ["PCP"],
    },
    {
        # Layout e sidebar próprios (não herda o shell da Central) — por isso
        # `url_externa` (não `url_name`): é o que faz o card abrir em nova
        # aba em templates/base.html, mesmo padrão do frete. Só o setor
        # FISCAL enxerga este módulo (ver contas/models.py::Setor.FISCAL) —
        # e como não é `url_name`, o SetorAccessMiddleware não cobre as URLs
        # do fiscal automaticamente: cada view em fiscal/views.py precisa do
        # próprio @setor_required (ver fiscal/views.py).
        "slug": "fiscal",
        "aba": "Fiscal",
        "nome": "Controle Fiscal",
        "subtitulo": "Saldo Fiscal — industrialização por encomenda",
        "descricao": (
            "Saldo de tecido/insumo recebido de clientes pra industrialização: "
            "entradas, retornos e consumo automático por NF, cliente e centro de custo."
        ),
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M9 15l2 2 4-4"/></svg>',
        "tags": ["Fiscal", "NF-e", "Saldo", "Industrialização"],
        "url_externa": reverse_lazy("fiscal:index"),
        "origem": "Módulo novo — Saldo Fiscal (industrialização por encomenda).",
        "setores": ["FISCAL"],
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
        "setores": ["PCP", "RH"],
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
        "setores": ["PCP", "RH"],
    },
    {
        "slug": "gut-visao-geral",
        "aba": "GUT",
        "nome": "Visão Geral",
        "subtitulo": "Mega Preven Matriz",
        "descricao": "Visão geral consolidada de apontamento (AppSheet — GESTÃOZanattex), ordenada por Eficiência.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["GUT", "AppSheet", "Visão Geral"],
        "url_externa": "https://www.appsheet.com/start/a4b353df-9713-42f0-a544-85e76ac93811#appName=GESTÃOZanattex-819603934&group=%5B%7B\"Column\"%3A\"Data\"%2C\"Order\"%3A\"Descending\"%7D%5D&page=fastTable&sort=%5B%7B\"Column\"%3A\"Eficiência\"%2C\"Order\"%3A\"Descending\"%7D%5D&table=MEGAPREVEN+MATRIZ&view=MEGAPREVEN+MATRIZ",
        "origem": "AppSheet (GESTÃOZanattex)",
        "setores": ["PCP", "RH"],
    },
    # ---------------- Planilhas (Google Sheets — fonte de dados de cada dashboard) ----------------
    # Links externos direto pra planilha de origem, mesmo padrão da GUT acima
    # (não têm url_name/slug de dashboard, abrem em aba nova).
    {
        "slug": "planilha-corte-arealva",
        "aba": "Planilhas",
        "nome": "Corte · Arealva (Mantas)",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Corte · Arealva (Mantas).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Corte"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1KLbNpw-P28YgoijXfMXU-zRQULuDHMMB/edit#gid=1544210185",
        "origem": "integracao/fontes.py::corte_arealva",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-corte-iacanga",
        "aba": "Planilhas",
        "nome": "Corte · Iacanga (Mantas Giattex)",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Corte · Iacanga (Mantas Giattex).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Corte"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1FBpCrq29_e1UBNwBlcgPTz66tbpUsgcgtzfXi4DcORU/edit#gid=0",
        "origem": "integracao/fontes.py::corte_iacanga",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-corte-itaju",
        "aba": "Planilhas",
        "nome": "Corte · Itaju",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Corte · Itaju (Ponto Palito Marcelino).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Corte"],
        "url_externa": "https://docs.google.com/spreadsheets/d/19dJKG956drBCv3fEnL75dTLf157xLKvE/edit#gid=1039503764",
        "origem": "integracao/fontes.py::corte_itaju",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-corte-lencol",
        "aba": "Planilhas",
        "nome": "Corte · Lençol (Arealva)",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Corte · Lençol (Arealva).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Corte"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1ypSEpTvIsm_hbgHmEf-v0fuR-P9h0mOa/edit#gid=1396046910",
        "origem": "integracao/fontes.py::corte_lencol",
        # Tem colunas de valor (VALOR_RECEBER/VALOR_PECA) — mesma régua da
        # aba Financeiro do dashboard de Lençol, restrita a admins.
        "admin_only": True,
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-corte-cortina",
        "aba": "Planilhas",
        "nome": "Corte · Cortina",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Corte · Cortina.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Corte"],
        "url_externa": "https://docs.google.com/spreadsheets/d/13h8T-cOo0aYwSikSfX-L3zyOks8uSODT/edit#gid=330898216",
        "origem": "integracao/fontes.py::corte_cortina",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-producao-faccoes-atual",
        "aba": "Planilhas",
        "nome": "Produção Facções (atual)",
        "subtitulo": "Planilha de origem — em uso",
        "descricao": "Planilha Google Sheets que alimenta hoje o dashboard de Análise de Produção (facções externas): uma aba por facção + guia de metas.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Produção"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1V05lVI-HlZXpGTc1p3R2V7ddnMTTjSOQ/edit",
        "origem": "integracao/fontes.py::FACCOES_SHEET_ID",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-producao-geral",
        "aba": "Planilhas",
        "nome": "Produção Facções (histórico até Jun/2026)",
        "subtitulo": "Planilha de origem — descontinuada",
        "descricao": "Planilha antiga de facções, usada até junho/2026 (substituída pela planilha atual, com uma aba por facção).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Produção", "Histórico"],
        "url_externa": "https://docs.google.com/spreadsheets/d/15s_ZttYG4UkSprgp4V_9gUBSgg7p8JRTiSQZL4xBi6Y/edit#gid=0",
        "origem": "integracao/fontes.py::producao_geral",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-producao-interna-littex",
        "aba": "Planilhas",
        "nome": "Produção Interna · LITTEX",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Produção · Colaboradores (LITTEX).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Produção Interna"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1wpCdsgLVv_R14yDkak6OMwXKJjUbvL9p/edit#gid=1697720285",
        "origem": "integracao/fontes.py::PRODUCAO_INTERNO::LITTEX",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-producao-interna-ggttex-jogos",
        "aba": "Planilhas",
        "nome": "Produção Interna · GGTTEX Jogos",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Produção · Colaboradores (GGTTEX Jogos).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Produção Interna"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1b8gCNUqZagkINAN1egnA7Va6g6Bv4esv/edit#gid=410924690",
        "origem": "integracao/fontes.py::PRODUCAO_INTERNO::GGTTEX_JOGOS",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-producao-interna-ggttex-fronha",
        "aba": "Planilhas",
        "nome": "Produção Interna · GGTTEX Fronha",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Produção · Colaboradores (GGTTEX Fronha).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Produção Interna"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1b8gCNUqZagkINAN1egnA7Va6g6Bv4esv/edit#gid=671875370",
        "origem": "integracao/fontes.py::PRODUCAO_INTERNO::GGTTEX_FRONHA",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-producao-interna-ggttex-cortina",
        "aba": "Planilhas",
        "nome": "Produção Interna · GGTTEX Cortina",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Produção · Colaboradores (GGTTEX Cortina).",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Produção Interna"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1PG5t_aWif2iJiCyEtgKE6sLFvMu7w5sL/edit#gid=296216772",
        "origem": "integracao/fontes.py::PRODUCAO_INTERNO::GGTTEX_CORTINA",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-carteira-pedidos",
        "aba": "Planilhas",
        "nome": "Carteira de Pedidos",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Carteira de Pedidos.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Controladoria"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1U-iNIQRqKOIBrDZ86ZE5uJW6IQCzugJ7/edit#gid=611396912",
        "origem": "integracao/fontes.py::carteira_pedidos",
        # Tem colunas de valor (VALOR_UNIT/VALOR_TOTAL) — mesma régua do
        # dashboard de Carteira de Pedidos, já admin_only.
        "admin_only": True,
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-programacao-corte",
        "aba": "Planilhas",
        "nome": "Programação de Corte",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Programação de Corte.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Controladoria"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1FeTwrPEBOcC6RmD_5zh8NQLwOrYO87XA/edit#gid=708887209",
        "origem": "integracao/fontes.py::programacao_corte",
        "setores": ["PCP"],
    },
    {
        "slug": "planilha-previsao-cargas",
        "aba": "Planilhas",
        "nome": "Cargas - Previsto x Realizado",
        "subtitulo": "Planilha de origem",
        "descricao": "Planilha Google Sheets que alimenta o dashboard de Previsão de Cargas.",
        "icone": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
        "tags": ["Planilha", "Google Sheets", "Logística"],
        "url_externa": "https://docs.google.com/spreadsheets/d/1RvC2dkk9KCribduCoxXM6sKGB0lxuIXk/edit",
        "origem": "integracao/fontes.py::previsao_cargas",
        # Tem coluna de valor (VALOR_FRETE) — mesma régua do dashboard de
        # Previsão de Cargas, já admin_only.
        "admin_only": True,
        "setores": ["PCP"],
    },
]

# Gestão de OP ainda em desenvolvimento — some do menu com a flag off (padrão
# em produção, ver central/settings.py::GESTAO_OP_HABILITADA), já que a
# rota correspondente (central/urls.py) também não existe nesse caso —
# senão sobraria um link morto na sidebar apontando pra um 404.
if not settings.GESTAO_OP_HABILITADA:
    MODULOS = [m for m in MODULOS if m["slug"] != "ops"]

# Ordem das abas na sidebar
ABAS = ["Controladoria", "Análise de Dados", "Relatórios", "Ferramentas", "Fiscal", "GUT", "Planilhas"]

# Ícone + rótulo curto de cada ABA (não módulo) — usados pelo rail de setores
# da sidebar (ver templates/base.html): cada aba vira um botão só de
# ícone+rótulo curto no rail, que abre um mini painel flutuante com os
# módulos daquela aba (paineis/context_processors.py monta esse agrupamento) —
# ou, quando só tem um módulo (caso de "Fiscal" hoje, só o Controle Fiscal),
# abre a página direto sem passar pelo painel (ver `link_direto` em
# paineis/context_processors.py). "Fiscal" só aparece pra quem tem
# `setores=["FISCAL"]` liberado (ver contas/models.py::Setor.FISCAL) —
# ninguém de outro setor vê esse ícone no rail.
ABA_ICONES = {
    "Controladoria": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12l2 2 4-4"/></svg>',
    "Análise de Dados": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
    "Relatórios": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>',
    "Ferramentas": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    # Balança — remete a "saldo/balanço fiscal", visualmente distinto do
    # ícone de documento já usado em Relatórios.
    "Fiscal": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M2 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/></svg>',
    "GUT": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
    "Planilhas": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
}

# Só as abas cujo nome não cabe inteiro sob o ícone no rail (84px de largura)
# precisam de um rótulo abreviado — as demais usam o próprio nome da aba.
ABA_LABEL_CURTO = {
    "Análise de Dados": "Dados",
}

MODULOS_POR_SLUG = {m["slug"]: m for m in MODULOS}


def modulos_por_aba(modulos=None):
    """Retorna {aba: [modulos]} preservando a ordem de ABAS. Aceita uma
    lista de módulos já filtrada (ex.: por setor — ver
    contas/permissions.py::modulos_liberados); sem argumento, usa todos."""
    agrupado = {aba: [] for aba in ABAS}
    for m in (modulos if modulos is not None else MODULOS):
        agrupado.setdefault(m["aba"], []).append(m)
    return agrupado

