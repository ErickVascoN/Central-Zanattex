from django.db import migrations
from django.db.models import F


def centro_custo_por_cnpj(apps, schema_editor):
    """centro_custo passou de nome fantasia (xFant/município) pro CNPJ da
    Zanattex na nota — emitente na saída, destinatário na entrada."""
    NotaFiscal = apps.get_model("fiscal", "NotaFiscal")
    NotaFiscal.objects.filter(tipo="SAIDA").update(centro_custo=F("emit_cnpj"))
    NotaFiscal.objects.filter(tipo="ENTRADA").update(centro_custo=F("dest_cnpj"))


class Migration(migrations.Migration):

    dependencies = [
        ('fiscal', '0004_vinculo_criterio'),
    ]

    operations = [
        migrations.RunPython(centro_custo_por_cnpj, migrations.RunPython.noop),
    ]
