from django.urls import path

from . import views

app_name = "fiscal"

urlpatterns = [
    path("", views.index, name="index"),

    path("importar/entrada/", views.importar_entrada, name="importar_entrada"),
    path("importar/entrada/lote/", views.lote_importacao_entrada, name="lote_importacao_entrada"),
    path("importar/entrada/revisao/", views.revisao_importacao_entrada, name="revisao_importacao_entrada"),
    path("importar/entrada/confirmar/", views.confirmar_importacao_entrada,
         name="confirmar_importacao_entrada"),
    path("importar/saida/", views.importar_saida, name="importar_saida"),
    path("importar/saida/lote/", views.lote_importacao_saida, name="lote_importacao_saida"),
    path("importar/saida/revisao/", views.revisao_importacao_saida, name="revisao_importacao_saida"),
    path("importar/saida/confirmar/", views.confirmar_importacao_saida,
         name="confirmar_importacao_saida"),

    path("relatorios/", views.relatorios, name="relatorios"),
    path("relatorios/saldo.pdf", views.relatorio_saldo_pdf, name="relatorio_saldo_pdf"),
    path("relatorios/saldo.xlsx", views.relatorio_saldo_xlsx, name="relatorio_saldo_xlsx"),

    path("historico/", views.historico, name="historico"),
    path("historico/<int:item_id>/", views.historico_detalhe, name="historico_detalhe"),
    path("historico/exportar.xlsx", views.historico_xlsx, name="historico_xlsx"),
    path("historico/exportar.pdf", views.historico_pdf, name="historico_pdf"),

    path("saldo-tecidos/", views.saldo_tecidos, name="saldo_tecidos"),
    path("saldo-tecidos/detalhe/", views.saldo_tecidos_detalhe, name="saldo_tecidos_detalhe"),

    path("pendencias/", views.pendencias, name="pendencias"),
    path("pendencias/<int:pendencia_id>/resolver/", views.resolver_pendencia, name="resolver_pendencia"),
]
