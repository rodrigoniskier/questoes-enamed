"""Decisões humanas compartilhadas pelo Admin e pela pré-auditoria."""

import logging
import uuid
from datetime import timedelta
from urllib.parse import urljoin

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import Questao
from .utils import enviar_email_status

logger = logging.getLogger(__name__)
CORRECTION_SIGNING_SALT = "pre_auditoria.correcao.v1"
MAX_PARECER = 20000


def _notificar_apos_commit(decision_id, base_url):
    try:
        notificar_decisao(decision_id, base_url)
    except DatabaseError as error:
        # A decisão já foi confirmada. Uma falha no recibo de envio não deve
        # transformar esse resultado em uma falsa falha de gravação.
        logger.error("Decision notification persistence failed for %s: %s", decision_id, type(error).__name__)


def registrar_decisao(
    questao_id, revisor, classificacao, parecer="", *, fingerprint=None, request_id=None, base_url=""
):
    from pre_auditoria.models import DecisaoPreAuditoria, ReciboDecisao, SolicitacaoCorrecao
    from pre_auditoria.services import fingerprint_questao, tem_pendencias_obrigatorias, validar_aprovacao

    if (
        not revisor
        or not revisor.is_authenticated
        or not (
            revisor.has_perm("pre_auditoria.review_parecerpreauditoria")
            or revisor.has_perm("questoes.change_questao")
        )
    ):
        raise PermissionDenied
    if classificacao not in dict(Questao.STATUS_CHOICES):
        raise ValidationError("Classificação inválida.")
    parecer = (parecer or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(parecer) > MAX_PARECER:
        raise ValidationError("As orientações devem ter no máximo 20.000 caracteres.")
    if classificacao in {"APROVADA_SUGESTOES", "REPROVADA"} and len(parecer) < 20:
        raise ValidationError("Apresente orientações fundamentadas com pelo menos 20 caracteres.")
    if request_id:
        try:
            request_id = uuid.UUID(str(request_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValidationError("Identificador da decisão inválido.") from error

    with transaction.atomic():
        question = (
            Questao.objects.select_for_update()
            .select_related("componente__periodo__semestre")
            .get(pk=questao_id)
        )
        if request_id:
            receipt = ReciboDecisao.objects.select_related("decisao").filter(pk=request_id).first()
            if receipt:
                existing = receipt.decisao
                if (existing.questao_id, existing.acao, existing.justificativa) != (
                    question.pk,
                    classificacao,
                    parecer,
                ):
                    raise ValidationError("Este envio já foi utilizado para outra decisão.")
                if existing.notificacao_estado in {"PENDENTE", "FALHOU"}:
                    transaction.on_commit(lambda: _notificar_apos_commit(existing.pk, base_url))
                return existing
        period = question.componente.periodo
        if not period.ativo or not period.semestre_id or not period.semestre.ativo:
            raise ValidationError("O semestre ou período desta questão está arquivado.")
        current_fingerprint = fingerprint_questao(question)
        if fingerprint and fingerprint != current_fingerprint:
            raise ValidationError("A questão mudou em outra sessão. Recarregue e confira a versão atual.")
        latest = question.decisoes_pre_auditoria.first()
        if (
            latest
            and question.status == classificacao
            and latest.acao == classificacao
            and latest.fingerprint == current_fingerprint
            and latest.justificativa == parecer
        ):
            if request_id:
                ReciboDecisao.objects.create(pk=request_id, decisao=latest)
            # A repetição não cria outro parecer nem outro link. Só falhas conhecidas
            # podem ser retomadas por nova ação explícita do avaliador.
            if latest.notificacao_estado in {"PENDENTE", "FALHOU"}:
                transaction.on_commit(lambda: _notificar_apos_commit(latest.pk, base_url))
            return latest
        report = question.pareceres_pre_auditoria.filter(arquivado_em__isnull=True).first()
        if classificacao in Questao.STATUS_APROVADOS:
            if report is None:
                raise ValidationError("Execute uma pré-auditoria atual antes de aprovar a questão.")
            validar_aprovacao(question, report, parecer)
        now = timezone.now()
        question.solicitacoes_correcao.filter(utilizada_em__isnull=True, expira_em__gt=now).update(
            expira_em=now
        )
        question.status = classificacao
        question.notified_status = ""
        question.save(update_fields=["status", "notified_status"])
        decision = DecisaoPreAuditoria.objects.create(
            questao=question,
            parecer=report,
            revisor=revisor,
            revisor_nome=revisor.get_username(),
            acao=classificacao,
            fingerprint=current_fingerprint,
            justificativa=parecer,
            notificacao_estado="SEM_ENVIO" if classificacao == "PENDENTE" else "PENDENTE",
            aprovacao_excepcional=bool(
                classificacao in Questao.STATUS_APROVADOS and report and tem_pendencias_obrigatorias(report)
            ),
        )
        if request_id:
            ReciboDecisao.objects.create(pk=request_id, decisao=decision)
        if classificacao == "REPROVADA" and report and report.fingerprint == current_fingerprint:
            SolicitacaoCorrecao.objects.create(
                questao=question,
                parecer=report,
                revisor=revisor,
                revisor_nome=revisor.get_username(),
                mensagem=parecer,
                expira_em=now + timedelta(days=7),
            )
        if classificacao != "PENDENTE":
            transaction.on_commit(lambda: _notificar_apos_commit(decision.pk, base_url))
        return decision


def notificar_decisao(decision_id, base_url=""):
    from pre_auditoria.models import DecisaoPreAuditoria
    from pre_auditoria.services import fingerprint_questao

    # O recibo é reservado ANTES do SMTP. Concorrentes não enviam o mesmo recibo.
    with transaction.atomic():
        decision = (
            DecisaoPreAuditoria.objects.select_for_update()
            .select_related("questao__componente")
            .get(pk=decision_id)
        )
        if decision.notificacao_estado not in {"PENDENTE", "FALHOU"}:
            return
        if (
            decision.questao.decisoes_pre_auditoria.first().pk != decision.pk
            or decision.questao.status != decision.acao
            or fingerprint_questao(decision.questao) != decision.fingerprint
        ):
            decision.notificacao_estado = "SUPERADO"
            decision.save(update_fields=["notificacao_estado"])
            return
        decision.notificacao_estado = "ENVIANDO"
        decision.save(update_fields=["notificacao_estado"])
    correction = decision.questao.solicitacoes_correcao.filter(
        utilizada_em__isnull=True, expira_em__gt=timezone.now(), parecer_id=decision.parecer_id
    ).first()
    correction_url = ""
    if base_url and correction:
        token = signing.dumps(
            {"nonce": str(correction.nonce), "fingerprint": decision.fingerprint},
            salt=CORRECTION_SIGNING_SALT,
        )
        correction_url = urljoin(base_url, reverse("pre_auditoria:correcao_professor", args=[token]))
    try:
        enviar_email_status(
            decision.questao,
            status=decision.acao,
            parecer=decision.justificativa,
            correction_url=correction_url,
        )
    except Exception as error:
        DecisaoPreAuditoria.objects.filter(pk=decision.pk).update(notificacao_estado="FALHOU")
        logger.error("Decision email failed for decision %s: %s", decision.pk, type(error).__name__)
        return
    now = timezone.now()
    with transaction.atomic():
        DecisaoPreAuditoria.objects.filter(pk=decision.pk).update(
            notificacao_estado="ENVIADO", email_enviado_em=now
        )
        Questao.objects.filter(pk=decision.questao_id, status=decision.acao).update(
            notified_status=decision.acao
        )
        if correction:
            correction.email_enviado_em = now
            correction.save(update_fields=["email_enviado_em"])
