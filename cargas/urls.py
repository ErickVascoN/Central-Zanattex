from django.urls import path

from . import views

app_name = "cargas"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("relatorio/", views.relatorio_pdf_view, name="relatorio_pdf"),
]
