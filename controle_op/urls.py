from django.urls import path

from . import views

app_name = "controle_op"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("<int:programacao_id>/", views.detalhe, name="detalhe"),
    path("<int:programacao_id>/fechamento.pdf", views.fechamento_pdf, name="fechamento_pdf"),
    path("<int:programacao_id>/corte/", views.registrar_corte, name="registrar_corte"),
    path("<int:programacao_id>/envio/", views.registrar_envio, name="registrar_envio"),
    path("<int:programacao_id>/retorno/", views.registrar_retorno, name="registrar_retorno"),
    path("<int:programacao_id>/faturamento/", views.confirmar_faturamento, name="confirmar_faturamento"),
]
