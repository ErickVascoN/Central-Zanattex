"""URLs raiz da Central de Dados Zanattex."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from contas import views as contas_views
from contas.forms import LoginComLimiteForm
from . import views as central_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "entrar/",
        auth_views.LoginView.as_view(
            template_name="contas/login.html",
            authentication_form=LoginComLimiteForm,
        ),
        name="login",
    ),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("contas/heartbeat/", contas_views.heartbeat, name="heartbeat"),
    path("manifest.webmanifest", central_views.manifest, name="manifest"),
    path("sw.js", central_views.service_worker, name="service_worker"),
    path("offline/", central_views.offline, name="offline"),
    path("integracao/", include("integracao.urls")),
    path("producao/", include("producao.urls")),
    path("relatorios/", include("relatorios.urls")),
    path("frete/", include("frete.urls")),
    path("corte/", include("corte.urls")),
    path("carteira/", include("carteira.urls")),
    path("cargas/", include("cargas.urls")),
    path("programacao/", include("programacao.urls")),
    path("metas/", include("metas.urls")),
    path("", include("paineis.urls")),
]
