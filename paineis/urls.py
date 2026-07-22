from django.urls import path

from . import views

app_name = "paineis"

urlpatterns = [
    path("", views.home, name="home"),
    path("modulo/<slug:slug>/", views.modulo, name="modulo"),
]
