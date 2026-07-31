from django.urls import path

from . import views

app_name = "corte"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("itaju/", views.itaju_dashboard, name="itaju"),
    path("lencol/", views.lencol_dashboard, name="lencol"),
    path("cortina/", views.cortina_dashboard, name="cortina"),
    path("relatorio/manta/", views.relatorio_manta_pdf, name="relatorio_manta_pdf"),
    path("relatorio/itaju/", views.relatorio_itaju_pdf, name="relatorio_itaju_pdf"),
    path("relatorio/lencol/", views.relatorio_lencol_pdf, name="relatorio_lencol_pdf"),
    path("relatorio/cortina/", views.relatorio_cortina_pdf, name="relatorio_cortina_pdf"),
    path("relatorio/consolidado/", views.relatorio_consolidado_pdf, name="relatorio_consolidado_pdf"),
]
