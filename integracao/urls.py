from django.urls import path

from . import views

app_name = "integracao"

urlpatterns = [
    path("fontes/", views.status_fontes, name="status_fontes"),
    path("fontes/sincronizar/", views.sincronizar_agora, name="sincronizar_agora"),
    path("fontes/limpar-cache/", views.limpar_cache, name="limpar_cache"),
]
