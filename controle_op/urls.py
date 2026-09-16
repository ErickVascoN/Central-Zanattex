from django.urls import path

from . import views

app_name = "controle_op"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("disparo-prestadores/", views.disparo_prestadores, name="disparo_prestadores"),
    path("<int:programacao_id>/", views.detalhe, name="detalhe"),
    path("<int:programacao_id>/fechamento.pdf", views.fechamento_pdf, name="fechamento_pdf"),
    path("<int:programacao_id>/corte/", views.registrar_corte, name="registrar_corte"),
    path("<int:programacao_id>/envio/", views.registrar_envio, name="registrar_envio"),
    path("<int:programacao_id>/producao/", views.registrar_producao, name="registrar_producao"),
    path("<int:programacao_id>/retorno/", views.registrar_retorno, name="registrar_retorno"),
    path("<int:programacao_id>/requisitado/", views.registrar_requisitado, name="registrar_requisitado"),
    path("<int:programacao_id>/faturamento/", views.confirmar_faturamento, name="confirmar_faturamento"),
    # Fase 2b — link do prestador, sem login (ver o aviso em views.py logo
    # acima das duas views). Prefixo próprio ("prestador/") pra não colidir
    # com <int:programacao_id> nem parecer mais uma rota interna comum.
    path("prestador/<str:token>/", views.prestador_lista, name="prestador_lista"),
    path("prestador/<str:token>/<int:programacao_id>/", views.prestador_op, name="prestador_op"),
]
