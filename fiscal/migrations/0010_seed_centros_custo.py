"""Popula CentroCusto com os CNPJs que hoje vêm hardcoded em
settings.FISCAL_CNPJS_ZANATTEX — sem isso, toda nota das duas unidades já em
produção seria rejeitada assim que o código parar de olhar pro settings (ver
fiscal/importador.py::identificar_nota). Nome é só um chute editável — trocar
pelo nome de verdade de cada unidade direto no admin depois do deploy."""
from django.db import migrations

_CNPJS_ATUAIS = [
    ("14601572000130", "Zanattex (unidade 1)"),
    ("64030122000103", "Zanattex (unidade 2)"),
]


def seed(apps, schema_editor):
    CentroCusto = apps.get_model("fiscal", "CentroCusto")
    for cnpj, nome in _CNPJS_ATUAIS:
        CentroCusto.objects.get_or_create(cnpj=cnpj, defaults={"nome": nome})


def remover(apps, schema_editor):
    CentroCusto = apps.get_model("fiscal", "CentroCusto")
    CentroCusto.objects.filter(cnpj__in=[cnpj for cnpj, _ in _CNPJS_ATUAIS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('fiscal', '0009_centro_custo_model'),
    ]

    operations = [
        migrations.RunPython(seed, remover),
    ]
