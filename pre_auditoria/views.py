import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.http import HttpResponseGone, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from questoes.models import ComponenteCurricular, Periodo, Questao, Semestre
from questoes.utils import enviar_email_status

from .forms import (
    AlternativasRevisaoFormSet,
    CorrecaoProfessorForm,
    DecisaoForm,
    EvidenciaRevisorForm,
    PedidoCorrecaoForm,
    QuestaoRevisaoForm,
)
from .models import (
    AlteracaoPreAuditoria,
    DecisaoPreAuditoria,
    EvidenciaRevisor,
    LotePreAuditoria,
    ParecerPreAuditoria,
    SolicitacaoCorrecao,
)
from .services import (
    POLICY_VERSION,
    executar_pre_auditoria,
    fingerprint_questao,
    snapshot_questao,
    validar_aprovacao,
)

logger = logging.getLogger(__name__)
CORRECTION_SIGNING_SALT = "pre_auditoria.correcao.v1"
MAX_APPROVAL_BATCH = 25
AUDIT_CLAIM_TIMEOUT = timedelta(minutes=2)


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
    approved_count = questions.filter(status="APROVADA").count()

    latest_audit = ParecerPreAuditoria.objects.filter(questao_id=OuterRef("pk")).order_by("-criado_em", "-pk")
    questions = questions.annotate(latest_audit_id=Subquery(latest_audit.values("pk")[:1])).order_by("-pk")
    page = Paginator(questions.filter(status="PENDENTE"), 25).get_page(request.GET.get("page"))
    audits = ParecerPreAuditoria.objects.filter(
        pk__in=[q.latest_audit_id for q in page.object_list if q.latest_audit_id]
    )
    audit_map = {audit.pk: audit for audit in audits}
    for question in page.object_list:
        question.latest_audit = audit_map.get(question.latest_audit_id)
        question.audit_is_current = bool(
            question.latest_audit
            and question.latest_audit.fingerprint == fingerprint_questao(question)
            and question.latest_audit.indicadores.get("politica") == POLICY_VERSION
        )
        question.audit_is_historical = bool(
            question.latest_audit and question.latest_audit.indicadores.get("politica") != POLICY_VERSION
        )

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
        },
    )


@reviewer_required
def detalhe(request, questao_id):
    question = get_object_or_404(
        Questao.objects.select_related("componente__periodo__semestre").prefetch_related("alternativas"),
        pk=questao_id,
    )
    audits = question.pareceres_pre_auditoria.all()
    latest_audit = audits.first()
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
            "parecer_atual": bool(
                latest_audit
                and latest_audit.fingerprint == fingerprint_questao(question)
                and latest_audit.indicadores.get("politica") == POLICY_VERSION
            ),
            "alteracoes": changes,
            "decisoes": decisions,
        },
    )


@reviewer_write_required
@require_http_methods(["GET", "POST"])
def editar(request, questao_id):
    with transaction.atomic():
        question = get_object_or_404(
            Questao.objects.select_for_update().select_related("componente__periodo__semestre"),
            pk=questao_id,
            status="PENDENTE",
        )
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
                before = snapshot_questao(question)
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
    with transaction.atomic():
        question = (
            Questao.objects.select_for_update()
            .select_related("componente__periodo__semestre")
            .filter(
                pk=questao_id,
                status="PENDENTE",
                componente__periodo__ativo=True,
                componente__periodo__semestre__ativo=True,
            )
            .first()
        )
        if question is None:
            raise ValidationError("Questão inexistente, arquivada ou já decidida.")
        report = question.pareceres_pre_auditoria.select_for_update().order_by("-criado_em", "-pk").first()
        if report is None:
            raise ValidationError("A questão ainda não tem parecer de pré-auditoria.")
        fingerprint = validar_aprovacao(question, report, justificativa)
        report.resultado = ParecerPreAuditoria.Resultado.APROVADA
        report.indicadores = {
            **report.indicadores,
            "confirmado_por_revisor": True,
        }
        report.save(update_fields=["resultado", "indicadores"])
        question.status = "APROVADA"
        question.save(update_fields=["status"])
        decision = DecisaoPreAuditoria.objects.create(
            questao=question,
            parecer=report,
            revisor=revisor,
            revisor_nome=revisor.get_username(),
            acao=DecisaoPreAuditoria.Acao.APROVADA,
            fingerprint=fingerprint,
            justificativa=justificativa,
        )
        transaction.on_commit(lambda: _notificar_aprovacao(question.pk))
        return decision


def _notificar_aprovacao(question_id):
    question = Questao.objects.filter(pk=question_id, status="APROVADA").first()
    if not question or question.notified_status == "APROVADA":
        return
    try:
        enviar_email_status(question)
        question.notified_status = "APROVADA"
        question.save(update_fields=["notified_status"])
    except Exception as error:
        logger.error("Approval notification failed for question %s: %s", question_id, type(error).__name__)


@reviewer_write_required
@require_POST
def aprovar(request, questao_id):
    form = DecisaoForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Justificativa inválida.")
        return redirect("pre_auditoria:detalhe", questao_id=questao_id)
    try:
        _aprovar_questao(questao_id, request.user, form.cleaned_data["justificativa"])
    except (ValidationError, Questao.DoesNotExist) as error:
        messages.error(
            request,
            "; ".join(error.messages)
            if isinstance(error, ValidationError)
            else "Questão indisponível para aprovação.",
        )
    else:
        messages.success(request, "Aprovação confirmada; o status oficial da questão foi atualizado.")
    return redirect("pre_auditoria:detalhe", questao_id=questao_id)


@reviewer_write_required
@require_POST
def aprovar_lote(request):
    raw_ids = request.POST.getlist("questoes")
    if not raw_ids or len(raw_ids) > MAX_APPROVAL_BATCH or any(not value.isdigit() for value in raw_ids):
        messages.error(request, f"Selecione entre 1 e {MAX_APPROVAL_BATCH} questões válidas.")
        return redirect("pre_auditoria:lista")
    question_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    if len(question_ids) > MAX_APPROVAL_BATCH:
        messages.error(request, f"O lote está limitado a {MAX_APPROVAL_BATCH} questões.")
        return redirect("pre_auditoria:lista")

    approved = 0
    failures = []
    for question_id in question_ids:
        try:
            _aprovar_questao(question_id, request.user, request.POST.get("justificativa", ""))
            approved += 1
        except (ValidationError, Questao.DoesNotExist) as error:
            failures.append(
                f"{question_id}: {('; '.join(error.messages)) if isinstance(error, ValidationError) else 'indisponível'}"
            )
    messages.success(request, f"{approved} aprovação(ões) confirmada(s).")
    for failure in failures:
        messages.warning(request, failure)
    return redirect("pre_auditoria:lista")


@reviewer_write_required
@require_POST
def solicitar_correcao(request, questao_id):
    question = get_object_or_404(Questao, pk=questao_id, status="PENDENTE")
    report = question.pareceres_pre_auditoria.order_by("-criado_em", "-pk").first()
    if report is None or report.fingerprint != fingerprint_questao(question):
        messages.error(request, "Execute uma pré-auditoria atual antes de solicitar correção.")
        return redirect("pre_auditoria:detalhe", questao_id=question.pk)
    form = PedidoCorrecaoForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Descreva as correções necessárias com pelo menos 20 caracteres.")
        return redirect("pre_auditoria:detalhe", questao_id=question.pk)

    now = timezone.now()
    with transaction.atomic():
        question = get_object_or_404(Questao.objects.select_for_update(), pk=questao_id, status="PENDENTE")
        report = question.pareceres_pre_auditoria.select_for_update().order_by("-criado_em", "-pk").first()
        if report is None or report.fingerprint != fingerprint_questao(question):
            messages.error(
                request, "A questão mudou. Execute nova pré-auditoria antes do pedido de correção."
            )
            return redirect("pre_auditoria:detalhe", questao_id=question.pk)
        outstanding = (
            SolicitacaoCorrecao.objects.select_for_update()
            .filter(questao=question, utilizada_em__isnull=True, expira_em__gt=now)
            .first()
        )
        if outstanding and outstanding.email_enviado_em:
            messages.warning(request, "Já existe um link válido enviado para esta questão.")
            return redirect("pre_auditoria:detalhe", questao_id=question.pk)
        if outstanding:
            correction = outstanding
        else:
            correction = SolicitacaoCorrecao.objects.create(
                questao=question,
                parecer=report,
                revisor=request.user,
                revisor_nome=request.user.get_username(),
                mensagem=form.cleaned_data["mensagem"],
                expira_em=now + timedelta(days=7),
            )
            DecisaoPreAuditoria.objects.create(
                questao=question,
                parecer=report,
                revisor=request.user,
                revisor_nome=request.user.get_username(),
                acao=DecisaoPreAuditoria.Acao.CORRECAO,
                fingerprint=report.fingerprint,
                justificativa=form.cleaned_data["mensagem"],
            )
    signed_token = signing.dumps(
        {"nonce": str(correction.nonce), "fingerprint": report.fingerprint},
        salt=CORRECTION_SIGNING_SALT,
    )
    correction_url = request.build_absolute_uri(
        reverse("pre_auditoria:correcao_professor", args=[signed_token])
    )
    try:
        send_mail(
            "Correções solicitadas para sua questão",
            f"Olá, {question.professor_nome}.\n\n{correction.mensagem}\n\nAcesse o link seguro para corrigir a questão: {correction_url}\n\nO link expira em 7 dias e só pode ser usado uma vez.",
            settings.DEFAULT_FROM_EMAIL,
            [question.professor_email],
            fail_silently=False,
        )
    except Exception as error:
        logger.error("Correction request email failed for question %s: %s", question.pk, type(error).__name__)
        messages.error(
            request,
            "O pedido foi registrado, mas o e-mail falhou. Não reenvie automaticamente; confira o SMTP.",
        )
    else:
        correction.email_enviado_em = timezone.now()
        correction.save(update_fields=["email_enviado_em"])
        messages.success(request, "Pedido de correção enviado ao professor responsável.")
    return redirect("pre_auditoria:detalhe", questao_id=question.pk)


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
        question = correction.questao
        if (
            question.status != "PENDENTE"
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
                before = snapshot_questao(question)
                form.save()
                alternative_formset.save()
                question.refresh_from_db()
                after = snapshot_questao(question)
                if before != after:
                    AlteracaoPreAuditoria.objects.create(
                        questao=question,
                        revisor=correction.revisor,
                        revisor_nome=correction.revisor_nome,
                        antes=before,
                        depois=after,
                        origem=AlteracaoPreAuditoria.Origem.PROFESSOR,
                        solicitacao_correcao=correction,
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
