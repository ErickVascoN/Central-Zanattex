from django.urls import path

from . import views

app_name = "integracao"

urlpatterns = [
    path("fontes/", views.status_fontes, name="status_fontes"),
    path("fontes/limpar-cache/", views.limpar_cache, name="limpar_cache"),
]
