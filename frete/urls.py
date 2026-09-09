from django.urls import path

from . import views

app_name = "frete"

urlpatterns = [
    path("", views.index, name="index"),
    path("calculadora/", views.calculadora_raw, name="calculadora_raw"),
    path("nota.pdf", views.nota_frete_pdf, name="nota_frete_pdf"),
    # persistência (substitui o Supabase)
    path("api/clientes/", views.clientes, name="clientes"),
    path("api/salvar/", views.salvar_calculo, name="salvar_calculo"),
    path("api/analytics/", views.analytics, name="analytics"),
    path("api/excluir/", views.excluir_calculo, name="excluir_calculo"),
    path("api/atualizar/", views.atualizar_calculo, name="atualizar_calculo"),
    path("api/ultimo-calculo/", views.ultimo_calculo_cliente, name="ultimo_calculo_cliente"),
]
