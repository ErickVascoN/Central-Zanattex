# Guia de Cálculos — Calculadora de Frete Zanattex

Este documento explica como cada item da calculadora é calculado, a origem dos dados e o que é automático x manual. Serve de referência para quem for usar, ajustar ou auditar os valores.

---

## 1. Distância e Ida e Volta

- Não há mapa embutido: você digita o destino (só atualiza o link do Google Maps) e confere a distância manualmente, preenchendo o campo **km**.
- **Ida e Volta = Sim** dobra o km efetivo usado em todos os cálculos que dependem de distância (combustível, seguro, depreciação).
- **Pedágio** funciona diferente: o valor digitado é sempre tratado como **ida (uma direção)**. Ao terminar de digitar (sair do campo) com "Ida e Volta = Sim" já marcado, o valor exibido dobra sozinho. Alternar o select também dobra/restaura automaticamente.

## 2. Combustível / Diesel (R$) — automático

```
Diesel = (km efetivo ÷ Consumo médio km/L) × Preço do Diesel R$/L
```

- **Consumo médio** é preenchido sozinho ao escolher o veículo (tabela `VEHICLE_CONSUMPTION`, baseada na planilha real de abastecimento Jan–Jul/2026). Pode ser sobrescrito manualmente.
- Carretas não têm motor — ao selecioná-las, a interface pede qual **cavalo mecânico** está puxando naquela viagem, e usa o consumo real desse cavalo.
- **Preço do Diesel** não é automático — tem um link "Consultar" (Petrobras) para conferência manual.

## 3. Motorista / Diárias (R$) — fixo, editável

- Padrão: **R$ 600,00/dia**, já preenchido no carregamento da página. Inclui diária e encargos. Os 12 dias do ciclo (viagem + descanso) são só referência do total trabalhado no mês, não multiplicam o valor.

## 4. Pedágio (R$) — manual, com ajuste automático de ida/volta

- Você digita o valor de **ida**. Com "Ida e Volta = Sim", o valor exibido é automaticamente dobrado (tanto ao alternar o select quanto ao digitar um novo valor).
- Link "Calcular no WebRouter" para consulta manual do valor.

## 5. Manutenção (R$) — manual

- Ainda não automatizado. Não há tabela histórica de manutenção por placa cadastrada ainda.

## 6. Arla 32 (R$) — automático

```
Arla = (25L, ou 50L se Ida e Volta) × Preço do Arla R$/L
```

- Consumo fixo de **25L por trecho** (não depende do km rodado nem do consumo de diesel), dobrando para 50L em ida e volta.
- Preço padrão: **R$ 2,20/L**, editável.

## 7. Depreciação (R$) — automático

```
Taxa R$/km = (Valor do veículo × 20% a.a.) ÷ Km rodado/ano da placa
Depreciação = Taxa R$/km × km efetivo da viagem
```

- **20% a.a.** é a taxa fiscal padrão de depreciação de caminhões no Brasil (vida útil de 5 anos).
- **Valor do veículo**: vem da planilha de veículos/seguradora (fotos "preço caminhões"), só para os veículos realmente em uso.
- **Km rodado/ano**: mesma tabela usada no seguro (`VEHICLE_ANNUAL_KM`), extraída do controle de abastecimento.
- Carretas usam o **próprio valor**, mas o km/ano do **cavalo mecânico** que as puxa (elas não rodam sozinhas).
- ⚠️ **Efeito importante**: como é custo fixo anual dividido pelo km rodado/ano, veículos com baixa quilometragem anual (ex: trucks usados esporadicamente) têm R$/km bem mais alto que veículos de uso intenso (ex: cavalos mecânicos), mesmo com valor de compra parecido. Não é bug — é o efeito de diluir um custo fixo em poucos km.

## 8. Seguro (R$) — automático

```
Taxa R$/km = Apólice anual da placa ÷ Km rodado/ano da placa
Seguro = Taxa R$/km × km efetivo da viagem
```

- Mesma lógica da depreciação (custo fixo anual ÷ km/ano), mas usando a **apólice real** de cada veículo (`VEHICLE_INSURANCE_ANNUAL` / `CARRETA_INSURANCE_ANNUAL`) em vez do valor do veículo.
- Carretas: apólice própria, km/ano do cavalo que está puxando.

## 9. Gerenciamento de Risco / GRIS — automático (parcial)

```
GRIS (R$) = Valor da Carga / NF (R$) × Risco (%)
```

- Diferente dos outros: é calculado sobre o **valor da mercadoria transportada**, não sobre custos da viagem.
- Ao digitar o valor da carga, o campo de **% de Risco** é preenchido com o padrão de mercado **0,3%** (só se estiver vazio — não sobrescreve ajuste manual).
- O % pode/deve variar conforme o grau de periculosidade do trajeto (rotas com mais risco de roubo → % mais alto).
- Mostrador "Total do GRIS (R$)" exibe o valor calculado em tempo real.

## 10. Impostos / ICMS / ISS (%) — manual, com padrão

- Padrão: **12%**, referente à guia única do **Simples Nacional** (ICMS, ISS, PIS e COFINS já unificados). Se a empresa for de outro regime, ajustar manualmente somando ICMS + ISSQN + PIS/COFINS.
- Aplicado sobre o custo base (custos + depreciação + risco), antes da margem de lucro.

## 11. Margem de Lucro desejada (%) — manual

- Aplicada por último, sobre o custo total já com impostos.

## 12. Multa (R$) — manual

- Ainda não automatizado. Não há dado histórico (planilha de multas) nem definição se representa multa de trânsito ou penalidade contratual.

---

## Fórmula final do Frete Total

```
Custos Core      = Diesel + Motorista + Pedágio + Manutenção + Arla + Multa + Seguro
Custo Base        = Custos Core + Depreciação + GRIS
Impostos          = Custo Base × Impostos (%)
Custos c/ Impostos = Custo Base + Impostos
Lucro             = Custos c/ Impostos × Margem de Lucro (%)
FRETE TOTAL       = Custos c/ Impostos + Lucro
```

## Percentuais do "Mapeamento de Custos Críticos" (cards à direita)

| Card | Composição |
|---|---|
| 1. Custos Variáveis | Diesel + Manutenção + Arla + Multa |
| 2. Custos Fixos | Motorista + Depreciação |
| 3. Gerenciamento de Risco | Seguro do veículo + GRIS |
| 4. Tributos | Impostos |
| 5. Pedágio | Pedágio |

Todos os percentuais são calculados sobre o **Frete Total** (já incluindo a margem de lucro).

---

## O que ainda é 100% manual (sem automação)

- Preço do Diesel (link de referência, mas digitação manual)
- Manutenção (R$)
- Multa (R$)
- % de Impostos (tem padrão, mas não é calculado)
- % de Margem de Lucro
- % de Risco (tem padrão de 0,3%, mas o valor real depende do trajeto)

## Dados usados nas tabelas automáticas

Todas as tabelas (`VEHICLE_CONSUMPTION`, `VEHICLE_INSURANCE_ANNUAL`, `VEHICLE_ANNUAL_KM`, `CARRETA_INSURANCE_ANNUAL`, `VEHICLE_VALUE`, `CARRETA_VALUE`) estão dentro do `<script>` no final do `index.html` e cobrem só os veículos atualmente em uso pela Zanattex. Para atualizar algum valor (troca de veículo, reajuste de apólice, etc.), basta editar essas constantes diretamente no código.
