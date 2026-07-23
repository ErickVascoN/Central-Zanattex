from django.urls import path

from . import views

app_name = "corte"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]
