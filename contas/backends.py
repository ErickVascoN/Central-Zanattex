"""Login sem diferenciar maiúsculas/minúsculas no usuário — o cadastro no
admin continua salvando como foi digitado, só a checagem no login ignora
o case (ninguém lembra se cadastrou "Erick" ou "erick")."""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None
        campo = f"{UserModel.USERNAME_FIELD}__iexact"
        try:
            user = UserModel._default_manager.get(**{campo: username})
        except UserModel.DoesNotExist:
            # roda o hasher mesmo assim, p/ não vazar (por timing) se o usuário existe
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            user = UserModel._default_manager.filter(**{campo: username}).order_by("pk").first()
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
