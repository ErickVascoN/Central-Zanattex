from django.urls import path

from . import views, cron

app_name = "relatorios"

urlpatterns = [
    path("", views.hub, name="hub"),
    path("cron/", cron.handle, name="cron"),
]
