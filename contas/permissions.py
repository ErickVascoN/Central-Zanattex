"""Helpers centrais de setor/permissão — único lugar com a regra "sem
perfil, setor em branco ou superuser = sem restrição de módulos". Reusado
pelo decorator (contas/decorators.py), pelo middleware
(contas/middleware.py) e pelo context processor da sidebar
(paineis/context_processors.py). Não mexe em admin_only/is_superuser, que
continua sendo a régua separada de dados financeiros/sensíveis."""
from __future__ import annotations


def get_setor(user) -> str:
    """Setor do usuário, ou "" se não tiver perfil/setor definido."""
    if not getattr(user, "is_authenticated", False):
        return ""
    perfil = getattr(user, "perfil", None)
    return perfil.setor if perfil else ""


def get_unidade(user) -> str:
    """Unidade de corte do usuário (só relevante pra setor=CORTE), ou ""."""
    perfil = getattr(user, "perfil", None)
    return perfil.unidade_corte if perfil else ""


def usuario_sem_restricao(user) -> bool:
    """True = vê todos os módulos, sem filtro de setor (superuser, ou
    usuário sem setor atribuído — comportamento de hoje, antes de existir
    PerfilUsuario)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return not get_setor(user)


def modulo_liberado(user, modulo: dict) -> bool:
    """Superuser ou usuário sem setor atribuído vê tudo (comportamento de
    hoje, retrocompatível — ninguém perde acesso "de graça" na migração,
    porque ninguém tem `setor` preenchido ainda). A partir do momento que
    alguém RECEBE um setor, a régua inverte: só enxerga os módulos
    explicitamente marcados com esse setor em `setores` — um módulo sem
    `setores` fica invisível pra quem tem setor atribuído (é assim que um
    usuário Corte fica restrito só à Gestão de Corte, em vez de continuar
    vendo tudo que não foi marcado)."""
    if usuario_sem_restricao(user):
        return True
    setores_permitidos = modulo.get("setores")
    if not setores_permitidos:
        return False
    return get_setor(user) in setores_permitidos


def modulos_liberados(user, modulos: list[dict]) -> list[dict]:
    """Filtra uma lista de módulos (ver paineis/modulos.py::MODULOS) pelos
    que o setor do usuário pode ver."""
    if usuario_sem_restricao(user):
        return list(modulos)
    return [m for m in modulos if modulo_liberado(user, m)]
