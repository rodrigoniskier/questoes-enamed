import logging
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, OuterRef, Subquery
from django.db.models.deletion import ProtectedError
from django.http import HttpResponseGone, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from questoes.avaliacao import registrar_decisao
from questoes.models import ComponenteCurricular, Periodo, Questao, Semestre
from questoes.views import gerar_alternativas_multipla_escolha

from .forms import (
    AlternativasRevisaoFormSet,
    CorrecaoProfessorForm,
    DecisaoForm,
    EvidenciaRevisorForm,
    ParecerInstitucionalForm,
    QuestaoRevisaoForm,
)
from .models import (
    AlteracaoPreAuditoria,
    DecisaoPreAuditoria,
    EvidenciaRevisor,
    LotePreAuditoria,
    OperacaoLimpezaPreAuditoria,
    ParecerPreAuditoria,
    SolicitacaoCorrecao,
)
from .services import (
    POLICY_VERSION,
    checklist_estrutural,
    executar_pre_auditoria,
    fingerprint_questao,
    snapshot_questao,
    tem_pendencias_obrigatorias,
)

logger = logging.getLogger(__name__)
CORRECTION_SIGNING_SALT = "pre_auditoria.correcao.v1"
MAX_APPROVAL_BATCH = 25
MAX_CLEANUP_QUESTIONS = 25
MAX_CLEANUP_REPORTS = 500
AUDIT_CLAIM_TIMEOUT = timedelta(minutes=2)
CLEANUP_CONFIRMATION_SALT = "pre_auditoria.limpeza.v1"


def _safe_next(request, candidate, fallback):
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


def _prepare_question(question):
    alternatives = question.alternativas.all().order_by("pk")
    if question.tipo_questao == "MULTIPLA_ESCOLHA":
        question.alternativas_finais_preview = gerar_alternativas_multipla_escolha(question)
    else:
        question.alternativas_finais_preview = list(alternatives)

    question.gabarito_preview = []
    for index, alternative in enumerate(question.alternativas_finais_preview):
        correct = alternative["eh_correta"] if isinstance(alternative, dict) else alternative.eh_correta
        if correct:
            text = alternative["texto"] if isinstance(alternative, dict) else alternative.texto
            question.gabarito_preview.append({"letra": chr(ord("A") + index), "texto": text})

    question.preview_image_available = False
    question.preview_image_url = ""
    if question.imagem:
        try:
            question.preview_image_available = question.imagem.storage.exists(question.imagem.name)
            if question.preview_image_available:
                question.preview_image_url = question.imagem.url
        except (OSError, ValueError):
            logger.warning("Question image unavailable for preview: %s", question.pk)


def _mark_audit_state(question, report):
    question.latest_audit = report
    question.audit_is_current = bool(
        report
        and report.arquivado_em is None
        and report.fingerprint == fingerprint_questao(question)
        and report.indicadores.get("politica") == POLICY_VERSION
    )
    question.audit_is_historical = bool(report and report.indicadores.get("politica") != POLICY_VERSION)
    question.approval_exception_needed = bool(
        question.audit_is_current and tem_pendencias_obrigatorias(report)
    )
    stored_checklist = (
        report.indicadores.get("validacoes_estruturais")
        if report and isinstance(report.indicadores, dict)
        else None
    )
    question.audit_checklist = (
        stored_checklist if isinstance(stored_checklist, list) else checklist_estrutural(question)
    )
    question.audit_findings = report.achados if report and isinstance(report.achados, list) else []


def _parse_cleanup_selection(post_data):
    raw_report_ids = post_data.getlist("parecer_ids")
    raw_question_ids = post_data.getlist("questao_ids")
    if (
        len(raw_report_ids) > MAX_CLEANUP_REPORTS
        or len(raw_question_ids) > MAX_CLEANUP_QUESTIONS
        or any(not value.isdigit() for value in raw_report_ids + raw_question_ids)
    ):
        raise ValidationError("A seleção de pareceres ou questões é inválida ou excede o limite.")
    return list(dict.fromkeys(map(int, raw_report_ids))), list(dict.fromkeys(map(int, raw_question_ids)))


def _cleanup_report_ids(report_ids, question_ids):
    if question_ids and Questao.objects.filter(pk__in=question_ids).count() != len(question_ids):
        raise ValidationError("Uma ou mais questões selecionadas não estão disponíveis.")
    if report_ids and ParecerPreAuditoria.objects.filter(pk__in=report_ids).count() != len(report_ids):
        raise ValidationError("Um ou mais pareceres selecionados não estão disponíveis.")

    candidate_ids = set(report_ids)
    if question_ids:
        latest_ids = {
            ParecerPreAuditoria.objects.filter(questao_id=question_id)
            .order_by("-criado_em", "-pk")
            .values_list("pk", flat=True)
            .first()
            for question_id in question_ids
        }
        candidate_ids.update(
            ParecerPreAuditoria.objects.filter(questao_id__in=question_ids)
            .exclude(pk__in=[pk for pk in latest_ids if pk is not None])
            .values_list("pk", flat=True)
        )
    if len(candidate_ids) > MAX_CLEANUP_REPORTS:
        raise ValidationError(
            f"A seleção inclui mais de {MAX_CLEANUP_REPORTS} pareceres. Reduza a quantidade de questões."
        )
    return sorted(candidate_ids)


def _cleanup_plan(report_ids, question_ids, lock=False):
    ids = _cleanup_report_ids(report_ids, question_ids)
    reports = ParecerPreAuditoria.objects.filter(pk__in=ids).order_by("pk")
    if lock:
        reports = reports.select_for_update()
    report_list = list(reports)
    latest_by_question = {}
    for report in ParecerPreAuditoria.objects.filter(
        questao_id__in={item.questao_id for item in report_list}
    ):
        current = latest_by_question.get(report.questao_id)
        if current is None or (report.criado_em, report.pk) > (current.criado_em, current.pk):
            latest_by_question[report.questao_id] = report
    latest_ids = {report.pk for report in latest_by_question.values()}

    deletable = []
    archivable = []
    not_eligible = []
    for report in report_list:
        if report.pk in latest_ids or report.arquivado_em is not None:
            not_eligible.append(report.pk)
            continue
        has_history = (
            report.evidencias.exists() or report.decisoes.exists() or report.solicitacoes_correcao.exists()
        )
        (archivable if has_history else deletable).append(report.pk)
    return {
        "requested_ids": ids,
        "deletable_ids": deletable,
        "archivable_ids": archivable,
        "not_eligible_ids": not_eligible,
        "not_eligible": len(not_eligible),
        "question_ids": question_ids,
    }


def _cleanup_confirmation(request, plan, return_to, status=200):
    confirmation = signing.dumps(
        {
            "requested_ids": plan["requested_ids"],
            "deletable_ids": plan["deletable_ids"],
            "archivable_ids": plan["archivable_ids"],
            "not_eligible_ids": plan["not_eligible_ids"],
            "question_ids": plan["question_ids"],
        },
        salt=CLEANUP_CONFIRMATION_SALT,
    )
    return render(
        request,
        "pre_auditoria/confirmar_limpeza.html",
        {
            "plano": plan,
            "confirmacao": confirmation,
            "return_to": return_to,
        },
        status=status,
    )


def reviewer_required(view):
    return login_required(
        permission_required("pre_auditoria.view_parecerpreauditoria", raise_exception=True)(view),
        login_url="admin:login",
    )


def reviewer_write_required(view):
    return login_required(
        permission_required("pre_auditoria.review_parecerpreauditoria", raise_exception=True)(view),
        login_url="admin:login",
    )


@reviewer_required
def lista(request):
    semestres = Semestre.objects.filter(ativo=True).order_by("-nome")
    periods = Periodo.objects.filter(ativo=True, semestre__ativo=True)
    semester_id = request.GET.get("semestre")
    period_id = request.GET.get("periodo")
    component_id = request.GET.get("componente")

    if semester_id and semester_id.isdigit():
        periods = periods.filter(semestre_id=semester_id)
    else:
        semester_id = ""
    if period_id and period_id.isdigit():
        periods = periods.filter(pk=period_id)
    else:
        period_id = ""

    components = ComponenteCurricular.objects.filter(
        periodo__in=periods, consolidado_em__isnull=True
    ).order_by("nome")
    if component_id and component_id.isdigit():
        components = components.filter(pk=component_id)
    else:
        component_id = ""

    questions = (
        Questao.objects.filter(componente__in=components)
        .select_related("componente__periodo__semestre")
        .prefetch_related("alternativas")
    )
    total_submitted = questions.count()
    pending_count = questions.filter(status="PENDENTE").count()
    approved_count = questions.filter(status__in=Questao.STATUS_APROVADOS).count()
    rejected_count = questions.filter(status="REPROVADA").count()
    status_filter = request.GET.get("status", "PENDENTE")
    if status_filter not in dict(Questao.STATUS_CHOICES) and status_filter != "TODAS":
        status_filter = "PENDENTE"

    latest_audit = ParecerPreAuditoria.objects.filter(
        questao_id=OuterRef("pk"), arquivado_em__isnull=True
    ).order_by("-criado_em", "-pk")
    questions = questions.annotate(
        latest_audit_id=Subquery(latest_audit.values("pk")[:1]),
        audit_count=Count("pareceres_pre_auditoria"),
    ).order_by("-pk")
    page = Paginator(
        questions if status_filter == "TODAS" else questions.filter(status=status_filter), 25
    ).get_page(request.GET.get("page"))
    audits = ParecerPreAuditoria.objects.filter(
        pk__in=[q.latest_audit_id for q in page.object_list if q.latest_audit_id]
    )
    audit_map = {audit.pk: audit for audit in audits}
    for question in page.object_list:
        _mark_audit_state(question, audit_map.get(question.latest_audit_id))
        _prepare_question(question)

    return render(
        request,
        "pre_auditoria/lista.html",
        {
            "semestres": semestres,
            "periodos": periods,
            "componentes": components,
            "semestre_selecionado": semester_id,
            "periodo_selecionado": period_id,
            "componente_selecionado": component_id,
            "questoes": page,
            "total_submetidas": total_submitted,
            "pendentes": pending_count,
            "aprovadas": approved_count,
            "reprovadas": rejected_count,
            "status_selecionado": status_filter,
            "status_choices": Questao.STATUS_CHOICES,
            "return_to": request.get_full_path(),
        },
    )


@reviewer_required
def detalhe(request, questao_id):
    question = get_object_or_404(
        Questao.objects.select_related("componente__periodo__semestre").prefetch_related("alternativas"),
        pk=questao_id,
    )
    audits = question.pareceres_pre_auditoria.select_related("revisor", "arquivado_por").all()
    latest_audit = audits.filter(arquivado_em__isnull=True).first()
    _mark_audit_state(question, latest_audit)
    _prepare_question(question)
    for report in audits:
        report.is_latest_unarchived = bool(latest_audit and report.pk == latest_audit.pk)
        report.audit_is_current = bool(
            report.is_latest_unarchived
            and report.fingerprint == fingerprint_questao(question)
            and report.indicadores.get("politica") == POLICY_VERSION
        )
        report.has_obligatory_findings = tem_pendencias_obrigatorias(report)
    changes = question.alteracoes_pre_auditoria.select_related("revisor")
    decisions = question.decisoes_pre_auditoria.select_related("revisor", "parecer")
    return render(
        request,
        "pre_auditoria/detalhe.html",
        {
            "questao": question,
            "pareceres": audits,
            "ultimo_parecer_id": latest_audit.pk if latest_audit else None,
            "politica_atual": POLICY_VERSION,
            "parecer_atual": question.audit_is_current,
            "alteracoes": changes,
            "decisoes": decisions,
        },
    )


@reviewer_write_required
@require_POST
def excluir_auditorias(request):
    fallback = reverse("pre_auditoria:lista")
    return_to = _safe_next(request, request.POST.get("next", ""), fallback)
    signed_plan = request.POST.get("plano_assinado", "")

    if signed_plan:
        if request.POST.get("confirmar_operacao") != "on":
            messages.error(request, "Marque a confirmação explícita antes de excluir ou arquivar pareceres.")
            return redirect(return_to)
        try:
            payload = signing.loads(signed_plan, salt=CLEANUP_CONFIRMATION_SALT, max_age=900)
            if not isinstance(payload, dict) or set(payload) != {
                "requested_ids",
                "deletable_ids",
                "archivable_ids",
                "not_eligible_ids",
                "question_ids",
            }:
                raise signing.BadSignature
        except signing.BadSignature:
            messages.error(
                request, "A confirmação expirou ou não é válida. Selecione os pareceres novamente."
            )
            return redirect(return_to)

        try:
            with transaction.atomic():
                question_ids = payload["question_ids"]
                if question_ids:
                    locked_questions = list(
                        Questao.objects.select_for_update()
                        .filter(pk__in=question_ids)
                        .values_list("pk", flat=True)
                    )
                    if len(locked_questions) != len(set(question_ids)):
                        raise ValidationError("Uma ou mais questões selecionadas não estão disponíveis.")
                plan = _cleanup_plan(payload["requested_ids"], payload["question_ids"], lock=True)
                for key in ("requested_ids", "deletable_ids", "archivable_ids", "not_eligible_ids"):
                    if plan[key] != payload[key]:
                        return _cleanup_confirmation(request, plan, return_to, status=409)
                archived_at = timezone.now()
                for report_id in plan["archivable_ids"]:
                    ParecerPreAuditoria.objects.filter(pk=report_id).update(
                        arquivado_em=archived_at,
                        arquivado_por=request.user,
                    )
                if plan["deletable_ids"]:
                    ParecerPreAuditoria.objects.filter(pk__in=plan["deletable_ids"]).delete()
                OperacaoLimpezaPreAuditoria.objects.create(
                    revisor=request.user,
                    revisor_nome=request.user.get_username(),
                    questao_ids=plan["question_ids"],
                    pareceres_solicitados=plan["requested_ids"],
                    pareceres_excluidos=plan["deletable_ids"],
                    pareceres_arquivados=plan["archivable_ids"],
                    nao_elegiveis=plan["not_eligible"],
                )
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect(return_to)
        except ProtectedError:
            logger.warning("Protected pre-audit report deletion rejected for reviewer %s", request.user.pk)
            messages.error(
                request,
                "Um parecer ganhou um registro administrativo durante a confirmação e foi preservado. Revise a seleção.",
            )
            return redirect(return_to)
        except DatabaseError as error:
            logger.error(
                "Pre-audit cleanup failed for reviewer %s: %s",
                request.user.pk,
                type(error).__name__,
            )
            messages.error(
                request,
                "Não foi possível concluir a limpeza com segurança. Nenhum parecer foi removido; revise e tente novamente.",
            )
            return redirect(return_to)

        messages.success(
            request,
            f"Limpeza concluída: {len(plan['deletable_ids'])} parecer(es) excluído(s) fisicamente, "
            f"{len(plan['archivable_ids'])} arquivado(s) para preservar histórico e "
            f"{plan['not_eligible']} não elegível(is) mantido(s).",
        )
        return redirect(return_to)

    try:
        report_ids, question_ids = _parse_cleanup_selection(request.POST)
        plan = _cleanup_plan(report_ids, question_ids)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
        return redirect(return_to)
    if not plan["requested_ids"]:
        messages.error(request, "Selecione pareceres anteriores ou questões para limpar.")
        return redirect(return_to)
    return _cleanup_confirmation(request, plan, return_to)


@reviewer_write_required
@require_http_methods(["GET", "POST"])
def editar(request, questao_id):
    with transaction.atomic():
        question = get_object_or_404(
            Questao.objects.select_for_update().select_related("componente__periodo__semestre"),
            pk=questao_id,
            status="PENDENTE",
        )
        before = snapshot_questao(question)
        current_fingerprint = fingerprint_questao(question)
        if request.method == "POST" and request.POST.get("fingerprint") != current_fingerprint:
            question_form = QuestaoRevisaoForm(request.POST, request.FILES, instance=question)
            alternative_formset = AlternativasRevisaoFormSet(
                request.POST, instance=question, prefix="alternativas"
            )
            question_form.is_valid()
            alternative_formset.is_valid()
            question_form.add_error(
                None, "A questão foi alterada em outra sessão. Confira os dados e reenvie."
            )
            return render(
                request,
                "pre_auditoria/editar.html",
                {
                    "questao": question,
                    "form": question_form,
                    "formset": alternative_formset,
                    "fingerprint": current_fingerprint,
                },
                status=409,
            )

        if request.method == "POST":
            question_form = QuestaoRevisaoForm(request.POST, request.FILES, instance=question)
            alternative_formset = AlternativasRevisaoFormSet(
                request.POST, request.FILES, instance=question, prefix="alternativas"
            )
            if question_form.is_valid() and alternative_formset.is_valid():
                question_form.save()
                alternative_formset.save()
                question.refresh_from_db()
                after = snapshot_questao(question)
                if before != after:
                    AlteracaoPreAuditoria.objects.create(
                        questao=question,
                        revisor=request.user,
                        revisor_nome=request.user.get_username(),
                        antes=before,
                        depois=after,
                    )
                    executar_pre_auditoria(question, revisor=request.user, semantica=False)
                messages.success(
                    request, "Alterações salvas. A estrutura foi reavaliada com o conteúdo atualizado."
                )
                return redirect("pre_auditoria:detalhe", questao_id=question.pk)
        else:
            question_form = QuestaoRevisaoForm(instance=question)
            alternative_formset = AlternativasRevisaoFormSet(instance=question, prefix="alternativas")

    return render(
        request,
        "pre_auditoria/editar.html",
        {
            "questao": question,
            "form": question_form,
            "formset": alternative_formset,
            "fingerprint": current_fingerprint,
        },
    )


@reviewer_write_required
@require_POST
def iniciar(request, questao_id):
    question = get_object_or_404(Questao, pk=questao_id, status="PENDENTE")
    executar_pre_auditoria(
        question,
        revisor=request.user,
        semantica=request.POST.get("analise_textual") == "on",
    )
    return redirect("pre_auditoria:detalhe", questao_id=question.pk)


@reviewer_write_required
@require_POST
def criar_lote(request):
    raw_ids = request.POST.getlist("questoes")
    if not raw_ids or len(raw_ids) > MAX_APPROVAL_BATCH or any(not value.isdigit() for value in raw_ids):
        messages.error(request, f"Selecione entre 1 e {MAX_APPROVAL_BATCH} questões pendentes.")
        return redirect("pre_auditoria:lista")
    question_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    valid_ids = set(
        Questao.objects.filter(
            pk__in=question_ids,
            status="PENDENTE",
            componente__periodo__ativo=True,
            componente__periodo__semestre__ativo=True,
        ).values_list("pk", flat=True)
    )
    if len(valid_ids) != len(question_ids):
        messages.error(request, "A seleção contém questão inexistente, arquivada ou já decidida.")
        return redirect("pre_auditoria:lista")
    batch = LotePreAuditoria.objects.create(
        revisor=request.user,
        revisor_nome=request.user.get_username(),
        questao_ids=question_ids,
    )
    return redirect("pre_auditoria:detalhe_lote", lote_id=batch.pk)


@reviewer_required
def detalhe_lote(request, lote_id):
    batch = get_object_or_404(LotePreAuditoria, pk=lote_id)
    reports = ParecerPreAuditoria.objects.filter(questao_id__in=batch.questao_ids).order_by("-criado_em")
    return render(
        request,
        "pre_auditoria/lote.html",
        {
            "lote": batch,
            "total": len(batch.questao_ids),
            "concluidas": batch.posicao,
            "pareceres": reports,
            "politica_atual": POLICY_VERSION,
            "process_url": reverse("pre_auditoria:processar_proximo", args=[batch.pk]),
        },
    )


@reviewer_write_required
@require_POST
def processar_proximo(request, lote_id):
    now = timezone.now()
    with transaction.atomic():
        batch = get_object_or_404(LotePreAuditoria.objects.select_for_update(), pk=lote_id)
        total = len(batch.questao_ids)
        if batch.status == LotePreAuditoria.Status.CONCLUIDO or batch.posicao >= total:
            batch.status = LotePreAuditoria.Status.CONCLUIDO
            batch.save(update_fields=["status", "atualizado_em"])
            return JsonResponse({"concluido": True, "posicao": batch.posicao, "total": total})
        if (
            batch.status == LotePreAuditoria.Status.PROCESSANDO
            and batch.iniciado_em
            and batch.iniciado_em > now - AUDIT_CLAIM_TIMEOUT
        ):
            return JsonResponse({"ocupado": True, "posicao": batch.posicao, "total": total}, status=202)

        question_id = batch.questao_ids[batch.posicao]
        question = Questao.objects.filter(
            pk=question_id,
            status="PENDENTE",
            componente__periodo__ativo=True,
            componente__periodo__semestre__ativo=True,
        ).first()
        if question is None:
            batch.posicao += 1
            batch.status = (
                LotePreAuditoria.Status.CONCLUIDO
                if batch.posicao >= total
                else LotePreAuditoria.Status.AGUARDANDO
            )
            batch.erro = f"A questão {question_id} foi arquivada ou decidida antes da execução."
            batch.claim_token = None
            batch.iniciado_em = None
            batch.save()
            return JsonResponse(
                {
                    "ignorada": True,
                    "erro": batch.erro,
                    "posicao": batch.posicao,
                    "total": total,
                    "concluido": batch.status == LotePreAuditoria.Status.CONCLUIDO,
                }
            )

        claim_token = uuid.uuid4()
        batch.status = LotePreAuditoria.Status.PROCESSANDO
        batch.claim_token = claim_token
        batch.iniciado_em = now
        batch.erro = ""
        batch.save(update_fields=["status", "claim_token", "iniciado_em", "erro", "atualizado_em"])

    fingerprint = fingerprint_questao(question)
    request_id = uuid.uuid5(batch.pk, f"{question.pk}:{fingerprint}")
    try:
        report = executar_pre_auditoria(
            question,
            revisor=batch.revisor,
            semantica=False,
            request_id=str(request_id),
        )
    except Exception as error:
        logger.error("Pre-audit batch item failed for question %s: %s", question.pk, type(error).__name__)
        with transaction.atomic():
            LotePreAuditoria.objects.filter(pk=batch.pk, claim_token=claim_token).update(
                status=LotePreAuditoria.Status.AGUARDANDO,
                claim_token=None,
                iniciado_em=None,
                erro="Falha ao persistir o parecer. O item não avançou; retome explicitamente.",
            )
        return JsonResponse(
            {"erro": "Falha ao salvar o parecer; retome explicitamente.", "posicao": batch.posicao},
            status=503,
        )

    with transaction.atomic():
        current = (
            LotePreAuditoria.objects.select_for_update().filter(pk=batch.pk, claim_token=claim_token).first()
        )
        if current is None:
            return JsonResponse({"erro": "A reserva do item mudou; recarregue o progresso."}, status=409)
        current.posicao += 1
        current.status = (
            LotePreAuditoria.Status.CONCLUIDO
            if current.posicao >= total
            else LotePreAuditoria.Status.AGUARDANDO
        )
        current.claim_token = None
        current.iniciado_em = None
        current.erro = ""
        current.save()
        return JsonResponse(
            {
                "posicao": current.posicao,
                "total": total,
                "resultado": (
                    "Apta para aprovação"
                    if report.indicadores.get("politica") == POLICY_VERSION
                    and report.resultado == ParecerPreAuditoria.Resultado.APROVADA
                    else report.get_resultado_display()
                ),
                "questao_id": question.pk,
                "concluido": current.status == LotePreAuditoria.Status.CONCLUIDO,
            }
        )


@reviewer_write_required
@require_POST
def registrar_evidencia(request, parecer_id):
    report = get_object_or_404(ParecerPreAuditoria.objects.select_related("questao"), pk=parecer_id)
    if report.fingerprint != fingerprint_questao(report.questao):
        return HttpResponseGone("O conteúdo da questão mudou. Execute nova pré-auditoria.")
    form = EvidenciaRevisorForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe o critério e a evidência documental ou humana.")
        return redirect("pre_auditoria:detalhe", questao_id=report.questao_id)
    criterion_id = form.cleaned_data["criterio_id"]
    inconclusive_ids = {item.get("criterion_id") for item in report.indicadores.get("inconclusivos", [])}
    if criterion_id not in inconclusive_ids:
        messages.error(request, "Esse critério não está inconclusivo neste parecer.")
        return redirect("pre_auditoria:detalhe", questao_id=report.questao_id)
    EvidenciaRevisor.objects.create(
        parecer=report,
        criterio_id=criterion_id,
        evidencia=form.cleaned_data["evidencia"],
        referencia=form.cleaned_data["referencia"],
        revisor=request.user,
        revisor_nome=request.user.get_username(),
    )
    messages.success(request, f"Conferência do critério {criterion_id} registrada.")
    return redirect("pre_auditoria:detalhe", questao_id=report.questao_id)


def _aprovar_questao(questao_id, revisor, justificativa=""):
    return registrar_decisao(questao_id, revisor, "APROVADA", justificativa)


def _parecer_response(request, question, data=None):
    _mark_audit_state(question, question.pareceres_pre_auditoria.filter(arquivado_em__isnull=True).first())
    latest = question.decisoes_pre_auditoria.first()
    form = ParecerInstitucionalForm(
        data=data,
        initial={
            "fingerprint": fingerprint_questao(question),
            "request_id": uuid.uuid4(),
            "classificacao": "",
            "parecer": latest.justificativa if latest else "",
        },
    )
    return form


@reviewer_write_required
@require_http_methods(["GET", "POST"])
def decidir(request, questao_id):
    question = get_object_or_404(Questao, pk=questao_id)
    form = _parecer_response(request, question, request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            decision = registrar_decisao(
                question.pk,
                request.user,
                form.cleaned_data["classificacao"],
                form.cleaned_data["parecer"],
                fingerprint=form.cleaned_data["fingerprint"],
                request_id=form.cleaned_data["request_id"],
                base_url=request.build_absolute_uri("/"),
            )
        except ValidationError as error:
            form.add_error(None, error)
        except DatabaseError as error:
            logger.error("Decision persistence failed: %s", type(error).__name__)
            form.add_error(
                None, "Não foi possível registrar a decisão. Seu texto foi preservado; tente novamente."
            )
        else:
            messages.success(request, f"Decisão registrada: {decision.get_acao_display()}.")
            return redirect("pre_auditoria:detalhe", questao_id=question.pk)
    return render(
        request,
        "pre_auditoria/parecer.html",
        {"questao": question, "form": form},
        status=400 if form.is_bound else 200,
    )


@reviewer_write_required
@require_POST
def aprovar(request, questao_id):
    form = DecisaoForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(str(error) for values in form.errors.values() for error in values))
    else:
        try:
            registrar_decisao(
                questao_id,
                request.user,
                "APROVADA",
                form.cleaned_data["justificativa"],
                base_url=request.build_absolute_uri("/"),
            )
        except (ValidationError, Questao.DoesNotExist) as error:
            messages.error(
                request,
                "; ".join(error.messages) if isinstance(error, ValidationError) else "Questão indisponível.",
            )
        else:
            messages.success(request, "Aprovação confirmada; o status oficial da questão foi atualizado.")
    return redirect(
        _safe_next(request, request.POST.get("next", ""), reverse("pre_auditoria:detalhe", args=[questao_id]))
    )


@reviewer_write_required
@require_POST
def aprovar_lote(request):
    raw_ids = request.POST.getlist("questoes")
    if not raw_ids or len(raw_ids) > MAX_APPROVAL_BATCH or any(not value.isdigit() for value in raw_ids):
        messages.error(request, f"Selecione entre 1 e {MAX_APPROVAL_BATCH} questões válidas.")
        return redirect("pre_auditoria:lista")
    approved = 0
    for question_id in dict.fromkeys(map(int, raw_ids)):
        try:
            registrar_decisao(
                question_id,
                request.user,
                "APROVADA",
                request.POST.get("justificativa", ""),
                base_url=request.build_absolute_uri("/"),
            )
        except (ValidationError, Questao.DoesNotExist) as error:
            messages.warning(
                request,
                f"{question_id}: "
                + (
                    "; ".join(error.messages)
                    if isinstance(error, ValidationError)
                    else "Questão indisponível."
                ),
            )
        else:
            approved += 1
    messages.success(request, f"{approved} aprovação(ões) confirmada(s).")
    return redirect("pre_auditoria:lista")


@reviewer_write_required
@require_POST
def solicitar_correcao(request, questao_id):
    # Compatibilidade com páginas antigas abertas antes desta atualização.
    data = request.POST.copy()
    data.update(
        {
            "classificacao": "REPROVADA",
            "parecer": request.POST.get("mensagem", ""),
            "confirmar": "on",
            "request_id": request.POST.get("request_id") or str(uuid.uuid4()),
        }
    )
    question = get_object_or_404(Questao, pk=questao_id)
    data["fingerprint"] = request.POST.get("fingerprint") or fingerprint_questao(question)
    form = ParecerInstitucionalForm(data)
    if form.is_valid():
        try:
            registrar_decisao(
                question.pk,
                request.user,
                "REPROVADA",
                form.cleaned_data["parecer"],
                fingerprint=form.cleaned_data["fingerprint"],
                request_id=form.cleaned_data["request_id"],
                base_url=request.build_absolute_uri("/"),
            )
        except ValidationError as error:
            form.add_error(None, error)
        except DatabaseError as error:
            logger.error("Decision persistence failed: %s", type(error).__name__)
            form.add_error(
                None, "Não foi possível registrar a decisão. Seu texto foi preservado; tente novamente."
            )
        else:
            messages.success(
                request, "Revisão necessária registrada; a devolutiva foi preparada para o professor."
            )
            return redirect("pre_auditoria:detalhe", questao_id=question.pk)
    return render(request, "pre_auditoria/parecer.html", {"questao": question, "form": form}, status=400)


@require_http_methods(["GET", "POST"])
def correcao_professor(request, token):
    max_age = 7 * 24 * 60 * 60
    try:
        payload = signing.loads(token, salt=CORRECTION_SIGNING_SALT, max_age=max_age)
    except signing.BadSignature:
        return HttpResponseGone("Este link expirou ou não é válido.")
    if not isinstance(payload, dict) or set(payload) != {"nonce", "fingerprint"}:
        return HttpResponseGone("Este link não é válido.")

    now = timezone.now()
    with transaction.atomic():
        correction = (
            SolicitacaoCorrecao.objects.select_for_update()
            .select_related("questao", "parecer")
            .filter(nonce=payload["nonce"])
            .first()
        )
        if correction is None or correction.utilizada_em is not None or correction.expira_em <= now:
            return HttpResponseGone("Este link já foi utilizado ou expirou.")
        question = Questao.objects.select_for_update().get(pk=correction.questao_id)
        before = snapshot_questao(question)
        if (
            question.status not in {"PENDENTE", "REPROVADA"}
            or correction.parecer.fingerprint != payload["fingerprint"]
            or fingerprint_questao(question) != payload["fingerprint"]
        ):
            return HttpResponseGone("A versão autorizada mudou ou não aceita mais correções por este link.")

        alternative_formset = AlternativasRevisaoFormSet(
            request.POST if request.method == "POST" else None,
            request.FILES if request.method == "POST" else None,
            instance=question,
            prefix="alternativas",
        )
        form = CorrecaoProfessorForm(
            request.POST if request.method == "POST" else None,
            request.FILES if request.method == "POST" else None,
            instance=question,
        )
        if request.method == "POST":
            if form.is_valid() and alternative_formset.is_valid():
                form.save()
                alternative_formset.save()
                question.refresh_from_db()
                after = snapshot_questao(question)
                if before != after:
                    AlteracaoPreAuditoria.objects.create(
                        questao=question,
                        revisor=None,
                        revisor_nome=question.professor_nome,
                        antes=before,
                        depois=after,
                        origem=AlteracaoPreAuditoria.Origem.PROFESSOR,
                        solicitacao_correcao=correction,
                    )
                question.status = "PENDENTE"
                question.notified_status = ""
                question.save(update_fields=["status", "notified_status"])
                DecisaoPreAuditoria.objects.create(
                    questao=question,
                    parecer=correction.parecer,
                    revisor=None,
                    revisor_nome=question.professor_nome,
                    acao="PENDENTE",
                    fingerprint=fingerprint_questao(question),
                    justificativa="Reenvio do professor para nova avaliação após reformulação.",
                    notificacao_estado="SEM_ENVIO",
                )
                correction.utilizada_em = timezone.now()
                correction.save(update_fields=["utilizada_em"])
                executar_pre_auditoria(question, revisor=correction.revisor, semantica=False)
                return render(request, "pre_auditoria/correcao_concluida.html", status=200)

    return render(
        request,
        "pre_auditoria/professor_correcao.html",
        {"questao": question, "form": form, "formset": alternative_formset, "mensagem": correction.mensagem},
    )
