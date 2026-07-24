"""URLs raiz da Central de Dados Zanattex."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "entrar/",
        auth_views.LoginView.as_view(template_name="contas/login.html"),
        name="login",
    ),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("integracao/", include("integracao.urls")),
    path("producao/", include("producao.urls")),
    path("relatorios/", include("relatorios.urls")),
    path("frete/", include("frete.urls")),
    path("corte/", include("corte.urls")),
    path("carteira/", include("carteira.urls")),
    path("", include("paineis.urls")),
]
