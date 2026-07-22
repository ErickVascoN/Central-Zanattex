from django.urls import path

from . import views

app_name = "producao"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("colaboradores/", views.colaboradores, name="colaboradores"),
]
