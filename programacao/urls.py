from django.urls import path

from . import views

app_name = "programacao"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("nova/", views.nova_programacao, name="nova_programacao"),
    path("nova/criar/", views.criar_programacao, name="criar_programacao"),
    path("nova/<int:programacao_id>/cancelar/", views.cancelar_programacao, name="cancelar_programacao"),
    path("nova/<int:programacao_id>/editar/", views.editar_programacao, name="editar_programacao"),
    path("nova/<int:programacao_id>/reprogramar/", views.reprogramar_programacao, name="reprogramar_programacao"),
    path("api/carteira-aberta/", views.api_carteira_aberta, name="api_carteira_aberta"),
    path("exportar/csv/", views.exportar_csv, name="exportar_csv"),
    path("exportar/pdf/", views.exportar_pdf, name="exportar_pdf"),
    path("exportar/imagem/", views.exportar_imagem, name="exportar_imagem"),
]
