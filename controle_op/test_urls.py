"""URLconf só de teste: `central/urls.py` só monta `controle-op/` quando
GESTAO_OP_HABILITADA está ligada (central/settings.py), e a flag é lida na
importação do módulo — override_settings não reconstrói a rota. Aqui a rota
entra sempre, sem mexer no resto (as demais views continuam reversíveis,
senão os templates base não renderizam)."""
from django.urls import include, path

from central.urls import urlpatterns as urlpatterns_base

# Na frente do catch-all de paineis, como em central/urls.py.
urlpatterns = [path("controle-op/", include("controle_op.urls"))] + list(urlpatterns_base)
