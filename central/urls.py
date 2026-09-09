"""URLs raiz da Central de Dados Zanattex."""
from django.conf import settings
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
]

# Gestão de OP ainda em desenvolvimento — com a flag off (padrão em produção,
# ver central/settings.py::GESTAO_OP_HABILITADA) a rota nem existe: acessar
# /controle-op/ dá 404 normal, não é só o link escondido do menu. Entra antes
# do catch-all de paineis (abaixo) por organização, mas não faria diferença
# de fato — paineis.urls não tem wildcard que colidisse com "controle-op/".
if settings.GESTAO_OP_HABILITADA:
    urlpatterns.append(path("controle-op/", include("controle_op.urls")))

urlpatterns.append(path("", include("paineis.urls")))
