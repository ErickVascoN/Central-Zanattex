from django.urls import path

from . import views

app_name = "producao"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("colaboradores/", views.colaboradores, name="colaboradores"),
    path("relatorio/faccoes.pdf", views.relatorio_faccoes_pdf, name="relatorio_faccoes_pdf"),
    path("relatorio/colaboradores.pdf", views.relatorio_colaboradores_pdf, name="relatorio_colaboradores_pdf"),
]
