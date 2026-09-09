from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import PerfilUsuario


class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    can_delete = False
    verbose_name_plural = "Perfil (setor)"


class CustomUserAdmin(UserAdmin):
    """UserAdmin padrão + o inline de setor/unidade — cadastrar ou editar um
    usuário já mostra o campo de Setor direto na mesma tela, sem UI nova."""
    inlines = (PerfilUsuarioInline,)
    list_display = UserAdmin.list_display + ("get_setor",)

    @admin.display(description="Setor")
    def get_setor(self, obj):
        perfil = getattr(obj, "perfil", None)
        return perfil.get_setor_display() if perfil and perfil.setor else "—"


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
