"""Exclusão e restauração administrativas do banco ativo."""

from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import Questao


@transaction.atomic
def arquivar_questoes(queryset, responsavel):
    if (
        not responsavel.is_active
        or not responsavel.is_staff
        or not responsavel.has_perm("questoes.delete_questao")
    ):
        raise PermissionDenied
    ids = list(queryset.select_for_update().values_list("pk", flat=True))
    return Questao.objects.filter(pk__in=ids).update(excluida_em=timezone.now(), excluida_por=responsavel)


def pode_restaurar_questoes(usuario):
    return usuario.is_active and usuario.is_staff and usuario.has_perm("questoes.change_questao")


@transaction.atomic
def restaurar_questoes(exclusoes_confirmadas, responsavel):
    if not pode_restaurar_questoes(responsavel):
        raise PermissionDenied

    excluidas = Questao.todos_objetos.filter(excluida_em__isnull=False)
    questoes = [
        questao
        for questao in excluidas.filter(pk__in=exclusoes_confirmadas)
        .only("enunciado", "excluida_em")
        .order_by("pk")
        .select_for_update()
        if questao.excluida_em.isoformat() == exclusoes_confirmadas[str(questao.pk)]
    ]
    if not questoes:
        return 0

    excluidas.filter(pk__in=[questao.pk for questao in questoes]).update(excluida_em=None, excluida_por=None)
    LogEntry.objects.log_actions(responsavel.pk, questoes, CHANGE, "Questão restaurada ao banco ativo.")
    return len(questoes)
