"""Edição e reenvio pelo banco de questões dos componentes."""

import hashlib
import json
import logging
import uuid
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.forms import inlineformset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from pre_auditoria.forms import QuestaoRevisaoForm, RevisaoAlternativasFormSet
from pre_auditoria.models import AlteracaoPreAuditoria, ReciboDecisao
from pre_auditoria.services import executar_pre_auditoria, snapshot_questao

from .avaliacao import AUTOR_REENVIO_SEM_LOGIN, registrar_reenvio
from .historico import orientacao_anterior
from .models import Alternativa, ComponenteCurricular, Questao
from .submission import ASSERTION_ANSWERS, allowed_styles

logger = logging.getLogger(__name__)
EDIT_SALT = "questoes.editar-reenviar.v1"


class QuestaoEnviadaForm(QuestaoRevisaoForm):
    confirmar_reenvio = forms.BooleanField(
        label="Confirmo o reenvio desta questão para avaliação como Pendente."
    )

    def __init__(self, *args, acesso_gestao, **kwargs):
        super().__init__(*args, **kwargs)
        if not acesso_gestao:
            for field in ("professor_nome", "professor_email", "componente", "uso_prova"):
                del self.fields[field]
            return

        self.fields["componente"].queryset = (
            ComponenteCurricular.objects.filter(
                periodo__semestre_id=self.instance.componente.periodo.semestre_id,
                periodo__ativo=True,
                periodo__semestre__ativo=True,
                consolidado_em__isnull=True,
            )
            .select_related("periodo__semestre")
            .order_by("periodo__nome", "nome")
        )

    def clean(self):
        data = super().clean()
        component = data.get("componente", self.instance.componente)
        if component and data.get("tipo_questao") not in allowed_styles(component):
            self.add_error("tipo_questao", "Este componente aceita apenas questões de resposta única.")
        return data

    class Meta(QuestaoRevisaoForm.Meta):
        fields = (
            "professor_nome",
            "professor_email",
            "componente",
            "tipo_questao",
            "uso_prova",
            "texto_base",
            "imagem",
            "enunciado",
            "proposicao_dois",
            "justificativa",
        )
        labels = {
            "professor_nome": "Nome do professor",
            "professor_email": "E-mail do professor",
            "componente": "Componente curricular",
            "tipo_questao": "Tipo da questão",
            "uso_prova": "Destino da questão",
            "texto_base": "Texto-base",
            "enunciado": "Enunciado / Proposição I",
            "proposicao_dois": "Proposição II",
        }


class AlternativasEnviadasFormSet(RevisaoAlternativasFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        expected = list(self.get_queryset().values_list("pk", flat=True))
        supplied = [form.cleaned_data.get("id") for form in self.initial_forms]
        if (
            self.initial_form_count() != len(expected)
            or [item.pk for item in supplied if item] != expected
            or any(form.cleaned_data.get("id") for form in self.extra_forms)
        ):
            raise forms.ValidationError(
                "As alternativas enviadas não correspondem a esta questão. Reabra a edição."
            )
        if self.instance.tipo_questao == "ASSERCAO_RAZAO":
            texts = [
                form.cleaned_data.get("texto", "").strip().casefold()
                for form in self.forms
                if not form.cleaned_data.get("DELETE") and form.cleaned_data.get("texto")
            ]
            canonical = [f"{letter}) {text}".casefold() for letter, text in ASSERTION_ANSWERS.items()]
            if texts != canonical:
                raise forms.ValidationError(
                    "Asserção-razão exige as cinco alternativas padrão, na ordem A a E."
                )


AlternativasEnviadas = inlineformset_factory(
    Questao,
    Alternativa,
    fields=("texto", "eh_correta"),
    widgets={"texto": forms.Textarea(attrs={"rows": 3})},
    formset=AlternativasEnviadasFormSet,
    extra=5,
    max_num=5,
    validate_max=True,
    can_delete=True,
)


def _snapshot(question):
    return {
        **snapshot_questao(question),
        "professor_nome": question.professor_nome,
        "professor_email": question.professor_email,
        "componente_id": question.componente_id,
        "componente_nome": str(question.componente),
        "status": question.status,
    }


def _version(question, snapshot):
    state = {
        **snapshot,
        "ultima_decisao": question.decisoes_pre_auditoria.values_list("pk", flat=True).first(),
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@require_http_methods(["GET", "POST"])
@never_cache
def editar_questao_enviada(request, questao_id):
    component_id = request.GET.get("componente", "")
    if not component_id.isdigit():
        return redirect("submeter_selecao")
    component = get_object_or_404(ComponenteCurricular, pk=component_id).canonico
    return_url = reverse("submeter_visualizar_questoes") + "?" + urlencode({"componente": component.pk})
    question = get_object_or_404(
        Questao.objects.select_related("componente__periodo__semestre"), pk=questao_id
    )
    autor = request.user if request.user.is_authenticated else None
    acesso_gestao = request.user.is_staff and request.user.has_perm("questoes.change_questao")
    sessao_edicao = None if autor else request.session.get("reenvio_nonce")
    token = request.POST.get("edicao_token", "") if request.method == "POST" else ""
    payload = None
    error = ""
    status = 200
    if request.method == "POST":
        try:
            payload = signing.loads(token, salt=EDIT_SALT, max_age=86400)
            if (
                not isinstance(payload, dict)
                or payload.get("user") != request.user.pk
                or (autor is None and (not sessao_edicao or payload.get("sessao") != sessao_edicao))
                or payload.get("questao") != question.pk
                or payload.get("componente") != component.pk
            ):
                raise ValueError
            uuid.UUID(payload["request_id"])
        except (signing.BadSignature, KeyError, ValueError, TypeError, AttributeError):
            error = "Esta edição expirou ou não é válida. Reabra a questão para editar novamente."
            status = 400
    # Um recibo válido também permite reconhecer o replay após mudança de componente.
    receipt = (
        ReciboDecisao.objects.filter(pk=payload["request_id"]).first() if payload and not error else None
    )
    if receipt and receipt.decisao.questao_id == question.pk:
        messages.info(request, "Este reenvio já foi concluído. Nenhuma alteração foi repetida.")
        return redirect(return_url)
    if question.componente_id not in component.ids_compartilhados():
        raise Http404
    period = question.componente.periodo
    if not period.ativo or not period.semestre_id or not period.semestre.ativo:
        error = "O semestre ou período desta questão está arquivado e não aceita alterações."
        status = 409

    original_image = question.imagem.name
    success_url = None
    form = QuestaoEnviadaForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        acesso_gestao=acesso_gestao,
        instance=question,
    )
    formset = AlternativasEnviadas(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        instance=question,
        prefix="alternativas",
    )
    try:
        with transaction.atomic():
            question = get_object_or_404(
                Questao.objects.select_for_update().select_related("componente__periodo__semestre"),
                pk=question.pk,
                componente_id__in=component.ids_compartilhados(),
            )
            original_image = question.imagem.name
            period = question.componente.periodo
            if not period.ativo or not period.semestre_id or not period.semestre.ativo:
                error = "O semestre ou período desta questão está arquivado e não aceita alterações."
                status = 409
            before = _snapshot(question)
            version = _version(question, before)
            if request.method == "GET":
                if autor is None:
                    sessao_edicao = request.session.setdefault("reenvio_nonce", uuid.uuid4().hex)
                token = signing.dumps(
                    {
                        "user": request.user.pk,
                        "sessao": sessao_edicao,
                        "questao": question.pk,
                        "componente": component.pk,
                        "version": version,
                        "request_id": str(uuid.uuid4()),
                    },
                    salt=EDIT_SALT,
                )
            # Revalidar o recibo sob o mesmo bloqueio da questão evita gravações duplicadas.
            elif not error and ReciboDecisao.objects.filter(pk=payload["request_id"]).exists():
                messages.info(request, "Este reenvio já foi concluído. Nenhuma alteração foi repetida.")
                return redirect(return_url)
            elif not error and payload.get("version") != version:
                error = "A questão ou seu parecer mudou em outra sessão. Seu texto foi preservado; confira a versão atual antes de reenviar."
                status = 409
            form = QuestaoEnviadaForm(
                request.POST if request.method == "POST" else None,
                request.FILES if request.method == "POST" else None,
                acesso_gestao=acesso_gestao,
                instance=question,
            )
            formset = AlternativasEnviadas(
                request.POST if request.method == "POST" else None,
                request.FILES if request.method == "POST" else None,
                instance=question,
                prefix="alternativas",
            )
            if request.method == "POST":
                valid_form, valid_alternatives = form.is_valid(), formset.is_valid()
                if error:
                    form.add_error(None, error)
                elif valid_form and valid_alternatives:
                    guidance = orientacao_anterior(question) or before.get("comentario_validacao", "")
                    form.save()
                    formset.save()
                    question.refresh_from_db()
                    executar_pre_auditoria(question, revisor=autor, request_id=payload["request_id"])
                    registrar_reenvio(question.pk, autor, request_id=payload["request_id"])
                    question.refresh_from_db()
                    after = _snapshot(question)
                    # O reenvio sem mudança também precisa ficar visível para a gestão.
                    AlteracaoPreAuditoria.objects.create(
                        questao=question,
                        revisor=autor,
                        revisor_nome=autor.get_username() if autor else AUTOR_REENVIO_SEM_LOGIN,
                        origem=(
                            AlteracaoPreAuditoria.Origem.REVISOR
                            if acesso_gestao
                            else AlteracaoPreAuditoria.Origem.PROFESSOR
                        ),
                        antes={**before, "_orientacao_reenvio": guidance},
                        depois=after,
                    )
                    success_url = (
                        reverse("submeter_visualizar_questoes")
                        + "?"
                        + urlencode({"componente": question.componente_id})
                    )
                else:
                    status = 400
    except (DatabaseError, OSError, ValidationError) as exception:
        logger.error("Question resubmission failed for %s: %s", questao_id, type(exception).__name__)
        if question.imagem.name and question.imagem.name != original_image and question.imagem._committed:
            try:
                question.imagem.storage.delete(question.imagem.name)
            except OSError:
                logger.error("Resubmission image cleanup failed for %s", questao_id)
        # O erro pode anteceder a validação dos formulários (ex.: bloqueio do banco).
        if not form.is_bound:
            raise
        form.is_valid()
        form.add_error(
            None,
            "Não foi possível concluir o reenvio. Nenhuma alteração foi salva; seus textos foram preservados. Tente novamente.",
        )
        question.refresh_from_db()
        status = 503

    if success_url is not None and status == 200:
        messages.success(
            request,
            "Questão salva e reenviada para avaliação. Status: Pendente. Os pareceres anteriores foram preservados.",
        )
        return redirect(success_url)

    return render(
        request,
        "questoes/editar_enviada.html",
        {
            "questao": question,
            "form": form,
            "formset": formset,
            "edicao_token": token,
            "return_url": return_url,
            "bloqueada": bool(error),
            "erro_edicao": error,
            "acesso_gestao": acesso_gestao,
            "pareceres": question.decisoes_pre_auditoria.exclude(justificativa="")[:5]
            if acesso_gestao
            else (),
            "assertion_answers": [f"{letter}) {text}" for letter, text in ASSERTION_ANSWERS.items()],
        },
        status=status,
    )
