"""Alguns lançamentos de "Bainha de Fronha"/"Bainha Fronha" aparecem por
engano dentro da planilha do GGTTEX Jogos (setor da planilha errada, mas a
peça foi realmente costurada). Para essas peças contarem na premiação
também precisa existir a regra de Bainha ou Fechamento sob a unidade
GGTTEX_JOGOS (mesmos valores da regra já cadastrada em GGTTEX_FRONHA)."""

from datetime import date

from django.db import migrations


def semear(apps, schema_editor):
    RegraPremiacao = apps.get_model("producao", "RegraPremiacao")
    origem = RegraPremiacao.objects.filter(
        unidade="GGTTEX_FRONHA", atividade="BAINHA_FECHAMENTO",
    ).order_by("-vigente_desde").first()
    meta_padrao = origem.meta_padrao if origem else 1800
    valor = origem.valor_peca_excedente if origem else "0.10"
    vigente_desde = origem.vigente_desde if origem else date.today().replace(day=1)

    RegraPremiacao.objects.get_or_create(
        unidade="GGTTEX_JOGOS", atividade="BAINHA_FECHAMENTO",
        vigente_desde=vigente_desde,
        defaults=dict(meta_padrao=meta_padrao, valor_peca_excedente=valor),
    )


def remover(apps, schema_editor):
    RegraPremiacao = apps.get_model("producao", "RegraPremiacao")
    RegraPremiacao.objects.filter(
        unidade="GGTTEX_JOGOS", atividade="BAINHA_FECHAMENTO",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("producao", "0002_seed_regras_premiacao"),
    ]

    operations = [
        migrations.RunPython(semear, remover),
    ]
