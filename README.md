# Central de Dados Zanattex

App **Django** que unifica os dashboards da empresa (portados da Central de Análise
em Streamlit) e a Calculadora de Frete em uma única interface profissional, com a
identidade da marca (azul-marinho + vermelho do logo).

> Projeto **novo e independente**. Não altera os projetos originais
> (Central Streamlit, Calculadora de Frete, Sistema de Controle de Produção) — eles
> permanecem intactos como backup. Aqui apenas reaproveitamos código/estrutura.
>
> Plano de conversão completo em **[PLANO_UNIFICACAO.md](PLANO_UNIFICACAO.md)**.

## Stack

- Python 3.13 + Django 5.2 LTS
- Templates Django + HTMX + Alpine.js (server-rendered, sem travamentos do Streamlit)
- CSS próprio com a paleta da marca (`static/css/app.css`)
- Banco: SQLite em desenvolvimento (Postgres na produção, fase posterior)

## Rodando (Windows)

O venv fica **fora** da pasta (que é sincronizada pelo OneDrive):

```powershell
# 1. Ativar o venv já criado
C:\Users\erick\venvs\central-zanattex\Scripts\activate

# 2. (se for a primeira vez) migrar o banco e criar usuário
python manage.py migrate
python manage.py createsuperuser

# 3. Rodar
python manage.py runserver
```

Acesse http://localhost:8000 — a home pede login e mostra os setores.

Usuário de teste criado no desenvolvimento: **erick** / **zanattex2026** (troque em produção).

## Estrutura

```
central/        # settings, urls raiz
contas/         # autenticação (login via templates próprios)
paineis/        # home, menu e registro de módulos (paineis/modulos.py)
templates/      # base.html (casca da marca), home, módulo, login
static/css/     # app.css — identidade visual
```

## Status (Fase 1 concluída)

- [x] Esqueleto do app com a identidade da marca
- [x] Login por usuário (Django auth)
- [x] Home com abas (Análise de Dados / Controladoria / Logística) e cards de setor
- [x] Todos os módulos registrados como placeholders navegáveis
- [x] Fase 2: camada de dados (`integracao`) — leitura ao vivo do Google Sheets
      (`python manage.py checar_fontes --todas`) e tela admin "Fonte de Dados"
- [ ] Fase 3: portar os dashboards, um a um
- [ ] Fase 4: módulo da Calculadora de Frete
