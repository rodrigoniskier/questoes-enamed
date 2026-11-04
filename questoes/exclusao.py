"""Exclusão administrativa do banco ativo, preservando todo o histórico."""

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
