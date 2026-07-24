from django.urls import path

from . import views

app_name = "cargas"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]
