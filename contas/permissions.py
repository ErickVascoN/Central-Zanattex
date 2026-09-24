"""Helpers centrais de setor/permissão — único lugar com a regra "só
superuser fica sem restrição de módulos; todo mundo mais só vê o que bate
com o setor do próprio perfil". Reusado pelo decorator
(contas/decorators.py), pelo middleware (contas/middleware.py) e pelo
context processor da sidebar (paineis/context_processors.py). Não mexe em
admin_only/is_superuser, que continua sendo a régua separada de dados
financeiros/sensíveis."""
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
    """True = vê todos os módulos, sem filtro de setor. Só superuser —
    usuário sem perfil ou sem setor atribuído NÃO é mais sem restrição
    (antes era; mudou a pedido explícito, pra ninguém entrar sem querer só
    porque esqueceram de cadastrar o setor dele)."""
    if not getattr(user, "is_authenticated", False):
        return False
    return user.is_superuser


def modulo_liberado(user, modulo: dict) -> bool:
    """Só superuser vê tudo. Todo mundo mais só enxerga os módulos
    explicitamente marcados com o setor do próprio perfil em `setores` — um
    módulo sem `setores` fica invisível pra qualquer não-superuser, e
    usuário sem perfil/setor não enxerga NENHUM módulo com `setores`
    definido (o que hoje é praticamente todos)."""
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
