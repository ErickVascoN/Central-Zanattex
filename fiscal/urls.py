from django.urls import path

from . import views

app_name = "fiscal"

urlpatterns = [
    path("", views.index, name="index"),

    path("importar/entrada/", views.importar_entrada, name="importar_entrada"),
    path("importar/entrada/confirmar/", views.confirmar_importacao_entrada,
         name="confirmar_importacao_entrada"),
    path("importar/saida/", views.importar_saida, name="importar_saida"),
    path("importar/saida/confirmar/", views.confirmar_importacao_saida,
         name="confirmar_importacao_saida"),

    path("relatorios/", views.relatorios, name="relatorios"),
    path("relatorios/saldo.pdf", views.relatorio_saldo_pdf, name="relatorio_saldo_pdf"),
    path("relatorios/saldo.xlsx", views.relatorio_saldo_xlsx, name="relatorio_saldo_xlsx"),

    path("historico/", views.historico, name="historico"),
    path("historico/<int:nota_id>/", views.historico_detalhe, name="historico_detalhe"),

    path("pendencias/", views.pendencias, name="pendencias"),
    path("pendencias/<int:pendencia_id>/resolver/", views.resolver_pendencia, name="resolver_pendencia"),
]
