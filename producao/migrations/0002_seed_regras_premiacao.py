"""Semeia as 10 regras de premiação do ANEXO I (Metas e Critérios de
Premiação por Produtividade — GGTTEX Jogos e Fronha), vigentes a partir de
hoje. Ajustáveis depois via admin (Regras de premiação)."""

from datetime import date

from django.db import migrations


# Estado histórico da migração não carrega os TextChoices do model real —
# usa os valores literais (mesmos definidos em producao/models.py).
GGTTEX_JOGOS, GGTTEX_FRONHA = "GGTTEX_JOGOS", "GGTTEX_FRONHA"


def _regras():
    # Início do mês corrente: a premiação vale para o fechamento do mês em
    # curso, não só a partir do dia em que a regra foi cadastrada.
    hoje = date.today().replace(day=1)

    def base(unidade, atividade, valor):
        return dict(unidade=unidade, atividade=atividade,
                    valor_peca_excedente=valor, vigente_desde=hoje)

    return [
        # 1a) Costura de Canto / Costura de Elástico — meta por tamanho,
        # 800 se mais de um tamanho no mesmo dia. R$0,20/pç excedente.
        dict(base(GGTTEX_JOGOS, "COSTURA_CANTO", "0.20"),
             usa_tamanho=True, meta_solteiro=900, meta_casal=800,
             meta_queen=700, meta_tamanhos_mistos=800),
        dict(base(GGTTEX_JOGOS, "COSTURA_ELASTICO", "0.20"),
             usa_tamanho=True, meta_solteiro=900, meta_casal=800,
             meta_queen=700, meta_tamanhos_mistos=800),
        # 1a) Dobra de Fundo / Dobra e Empapela — planilha não registra
        # tamanho nesses lançamentos, meta fixa de 800/dia. R$0,12/pç.
        dict(base(GGTTEX_JOGOS, "DOBRA_FUNDO", "0.12"), meta_padrao=800),
        dict(base(GGTTEX_JOGOS, "DOBRA_EMPAPELA", "0.12"), meta_padrao=800),
        # 1b) Costura Reta — meta fixa 350/dia. R$0,30/pç.
        dict(base(GGTTEX_JOGOS, "COSTURA_RETA", "0.30"), meta_padrao=350),
        # 3) Caseado e Embalagem — meta fixa 1300/dia cada. R$0,08/pç.
        dict(base(GGTTEX_JOGOS, "CASEADO", "0.08"), meta_padrao=1300),
        dict(base(GGTTEX_JOGOS, "EMBALAGEM", "0.08"), meta_padrao=1300),
        # 2a) Bainha ou Fechamento — meta fixa 1800/dia. R$0,10/pç.
        dict(base(GGTTEX_FRONHA, "BAINHA_FECHAMENTO", "0.10"),
             meta_padrao=1800),
        # 2b) Esticado e Desvirado — meta fixa 3000/dia cada, taxas distintas.
        dict(base(GGTTEX_FRONHA, "ESTICADO", "0.03"), meta_padrao=3000),
        dict(base(GGTTEX_FRONHA, "DESVIRADO", "0.04"), meta_padrao=3000),
    ]


def semear(apps, schema_editor):
    RegraPremiacao = apps.get_model("producao", "RegraPremiacao")
    for r in _regras():
        RegraPremiacao.objects.create(**r)


def remover(apps, schema_editor):
    RegraPremiacao = apps.get_model("producao", "RegraPremiacao")
    RegraPremiacao.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("producao", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(semear, remover),
    ]
