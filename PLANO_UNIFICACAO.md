# Plano — Nova Central de Dados Zanattex (Django)

> **Objetivo:** portar a Central de Análise (Streamlit, hoje em `localhost:8501`)
> para um **novo app Django**, reaproveitando o **design/estrutura** do projeto de
> Controle de Produção — **sem** trazer a lógica de PCP dele. Mesmos setores, mesmos
> dashboards, mesmos indicadores; só mais profissional, consistente e sem os
> travamentos do Streamlit. A **Calculadora de Frete** entra como módulo.
>
> Status: **escopo fechado** · Data: 22/07/2026

---

## 1. O que muda e o que NÃO muda

**Não muda (preservado 100%):**
- Todos os setores e dashboards que existem hoje na Central.
- Todos os indicadores/KPIs/gráficos de cada setor.
- A forma de alimentar: **preencher a planilha do Google Sheets continua atualizando
  os dashboards automaticamente**, com a mesma frescura de hoje (cache de 1–5 min +
  botão "Atualizar Dados").
- A home com as duas abas: **Análise de Dados** e **Controladoria**.

**Muda (para melhor):**
- Streamlit → **Django** (server-rendered + HTMX): sem re-rodar tudo a cada clique.
- Visual e navegação únicos (menu lateral, header, cards) — o design do projeto de
  Controle de Produção, sem a lógica de PCP.
- Login real por usuário (Django auth) no lugar da senha única.
- A **Calculadora de Frete** deixa de ser um HTML avulso e vira módulo da Central.

---

## 2. Mapa dos módulos (nada se perde)

Cada dashboard Streamlit vira um app/rota Django, mantendo o mesmo conteúdo.

### Aba "Análise de Dados"

| Dashboard hoje (Streamlit) | Vira módulo | Mantém |
|---|---|---|
| Análise de Corte (`3_Controle_de_Corte`) | `corte_analise` | Metas diárias por estação, produção, OPs, cores, indicadores por operador, ranking |
| Análise de Produção (`2_Producao_Geral` + `5_Producao_Faccoes`) | `producao` | Por Cliente (multi-empresa, metas, evolução) e Por Colaborador (LITTEX/GGTTEX, ranking, externos) |
| Previsão de Cargas (`8_Previsao_Cargas`) | `cargas` | Previsão × realizado, aderência por destino, timeline |
| Análise de Metas / Custos (`7_Plano_de_Metas`) | `metas` | Metas por prestador/unidade, projeção, custos *(admin)* |

### Aba "Controladoria"

| Dashboard hoje (Streamlit) | Vira módulo | Mantém |
|---|---|---|
| Programação de Corte (`4_Controladoria_Programacao`) | `programacao` | Planejado × realizado, status por OP (Pendente/Parcial/Concluído) |
| Controle de OP (`11_Controle_de_OP`) | `ops` | Status, % conclusão, histórico de fechamento, PDF |
| Carteira de Pedidos (`9_Carteira_de_Pedidos`) | `carteira` | Análise por cliente/categoria/tamanho/região, evolução mensal |
| Relatórios (`10_Relatorios`) | `relatorios` | Geração de PDFs de todos os módulos |
| Histórico de Dados (`6_Historico`) | `historico` | Backup do banco, consulta por período, export CSV *(admin)* |

### Novo módulo

| — | `frete` | Calculadora de Frete + central de análise (totais por mês/cliente/indicador) |

---

## 3. Camada de dados (a chave do "atualiza sozinho")

O comportamento de auto-atualização **não vem do Streamlit** — vem de a fonte ser o
Google Sheets, lido ao vivo. Portamos essa camada:

- **Loaders portados** de `utils/` (Central) para um app `integracao/` no Django:
  `cache_manager`, `faccao_loader`, `producao_interno_loader`, `date_parser`,
  `normalize`, `gid_detector`, `lencol_caseamento`, `faccoes_metas_calc`, `pdf_report`.
  **Essa lógica é reaproveitada quase 100%.**
- **Dashboards leem o Sheets ao vivo** (mesmos TTLs de hoje) → frescura idêntica.
- **Cópia em segundo plano no banco** (Postgres) como histórico permanente — substitui
  o backup SQLite atual (`db_manager` / módulo Histórico), sem atrasar o dado exibido.
- **Calculadora** usa o banco (grava cálculos) — migração do schema Supabase atual.

Gráficos renderizados com **Plotly.js** no template (mesmos gráficos de hoje), filtros
via HTMX (atualizam só a área do gráfico, sem recarregar a página).

---

## 4. Autenticação e acesso

- **Django auth** (já configurado no projeto, com grupos por setor).
- Substitui a senha única do Streamlit e a senha no código do HTML da calculadora.
- Os cards `admin_only` de hoje (Metas, Histórico) viram permissão/grupo Django.

---

## 5. Identidade visual (DEFINIDA)

**Adotar a identidade da marca Zanattex**, extraída do logo (pasta da Calculadora de
Frete) e já codificada na calculadora:

- **Azul-marinho** (`blue-950 #172554` → `slate-900 #0f172a`) — menu lateral e header.
- **Vermelho** (`red-600 #dc2626`) — acento (marca, item ativo, destaques).
- **Branco** sobre fundo claro (`#f4f6fb`) — cards e conteúdo.
- Wordmark **Z**ANATTE**X** (Z e X em vermelho), fonte Inter.

Estrutura de layout emprestada do projeto de Controle de Produção (menu, header,
cards), com a paleta trocada para a da marca. Mockup de referência aprovado.

---

## 6. Roadmap por fases

**Fase 1 — Esqueleto do novo app** *(a "estrutura melhor")*
- Novo projeto/app Django reusando `base.html`, menu lateral, paleta e componentes.
- Home com as duas abas (Análise de Dados / Controladoria) e os cards de setor.
- Login por usuário. Módulos como placeholder navegável.
- **Entrega:** a casca profissional, navegável, com a home igual à de hoje (melhor vestida).

**Fase 2 — Camada de dados (`integracao`)**
- Portar loaders + cache; comando de leitura ao vivo; cópia ao banco em segundo plano.
- **Entrega:** dados do Sheets disponíveis no novo app, atualizando como hoje.

**Fase 3 — Dashboards, um a um**
- Portar por ordem de uso (Corte → Produção → Metas → Programação → OP → Carteira →
  Cargas → Relatórios → Histórico), cada um com seus gráficos (Plotly.js) e KPIs.
- **Entrega:** cada setor sai do Streamlit e entra no novo app, sem lentidão.

**Fase 4 — Módulo `frete`**
- Portar a lógica de cálculo do HTML para serviço Python; telas de simulador e análise;
  migrar dados do Supabase.
- **Entrega:** calculadora integrada, mesmo login e banco.

**Fase 5 — Desligamento**
- Aposentar o Streamlit e o Supabase. Um app, um banco, um login.

---

## 7. Próximo passo sugerido

Aprovar o tema visual (seção 5) e **começar pela Fase 1** — montar o esqueleto do novo
app com a home e o menu, para você já ver a "estrutura melhor" funcionando. Os
dashboards entram em seguida, um a um, sem perder nenhum indicador.
