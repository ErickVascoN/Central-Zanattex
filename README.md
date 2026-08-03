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
- Templates Django + HTMX + Alpine.js + Plotly (server-rendered, sem travamentos do Streamlit)
- CSS próprio com a paleta da marca (`static/css/app.css`)
- Banco: SQLite em desenvolvimento, **Postgres em produção** (Fly Postgres) — os dados dos
  dashboards vêm do Google Sheets, sincronizados em background para o Postgres (ver `integracao/`)
- Deploy: **Fly.io** (`fly.toml`), gunicorn com 1 worker + agendador de sincronização
  em background no mesmo processo (`integracao/scheduler.py`)
- PWA: instalável na tela inicial (Android/iOS/desktop), com service worker

## Rodando (Windows)

```powershell
# 1. Ativar o venv (.venv na raiz do projeto)
.venv\Scripts\Activate.ps1

# 2. (se for a primeira vez) migrar o banco e criar usuário
python manage.py migrate
python manage.py createsuperuser

# 3. Rodar
python manage.py runserver
```

Acesse http://localhost:8000 — a home pede login e mostra os setores.

## Deploy (produção)

```bash
fly deploy
```

Builda direto do diretório local (não depende de commit/push) e faz rolling deploy na
máquina do Fly (`central-zanattex`). Sempre commitar antes de deployar, pra manter o
histórico do git batendo com o que está no ar.

## Estrutura

```
central/        # settings, urls raiz, middleware de rate limit
contas/         # autenticação (login, limite de tentativas)
paineis/        # home, menu e registro de módulos (paineis/modulos.py)
integracao/     # sincronização Google Sheets → Postgres (scheduler em background)
relatorios/     # hub central de geração de relatórios PDF
producao/       # Produção · Facções e · Colaboradores (dashboard + PDF)
corte/          # Corte · Manta (Arealva/Iacanga), Itaju, Lençol, Cortina
carteira/       # Carteira de Pedidos
cargas/         # Previsão de Cargas (Previsto × Realizado)
metas/          # Plano de Metas
frete/          # Calculadora de Frete (iframe)
templates/      # base.html (casca da marca), dashboards, relatórios, login
static/css/     # app.css — identidade visual
```

## Segurança

- **HTTPS obrigatório** em produção (`SECURE_SSL_REDIRECT`, HSTS), cookies de
  sessão/CSRF `Secure` + `HttpOnly`, `CSRF_TRUSTED_ORIGINS` configurado para o domínio
  do Fly.
- **Login com limite de tentativas** (`contas/`): 3 senhas erradas seguidas bloqueia o
  usuário; o bloqueio dobra a cada vez que volta a errar depois de liberado
  (1 → 2 → 4 → 8 min... até um teto de 60 min). Zera sozinho num login certo.
- **Rate limit por IP** (`central/middleware.py`): protege a máquina única de ficar
  sobrecarregada sob tráfego abusivo/automatizado. Dois patamares — anônimo (tela de
  login) e autenticado (bem mais folgado, pra não bloquear um escritório inteiro atrás
  do mesmo IP/NAT por engano).
- Dados vindos de fontes externas (Google Sheets) que acabam renderizados em página
  nunca vão direto pra um `<script>` via `json.dumps()|safe` — sempre `|json_script`
  (escapa `</script>` e afins), ver `templates/relatorios/hub.html`.
- Todas as views exigem `@login_required`; telas administrativas (sincronização manual,
  cache técnico) exigem `@staff_member_required`.

## Status

- [x] Fase 1: esqueleto do app com a identidade da marca, login, home com módulos
- [x] Fase 2: camada de dados (`integracao`) — sincronização Google Sheets → Postgres
      em background, tela admin "Fonte de Dados"
- [x] Fase 3: dashboards portados (Produção Facções/Colaboradores, Corte, Carteira,
      Cargas, Metas, Central de Relatórios em PDF)
- [x] Fase 4: Calculadora de Frete integrada (iframe, persistência no banco do app)
- [x] Deploy em produção (Fly.io + Fly Postgres), PWA instalável
