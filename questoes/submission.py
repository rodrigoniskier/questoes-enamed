"""Submission rules shared by the form, views and AI response validation."""

import uuid

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Alternativa, ComponenteCurricular, SubmissionReceipt

FORM_ROUTES = {
    "RESPOSTA_UNICA": "submeter_resposta_unica",
    "MULTIPLA_ESCOLHA": "submeter_resposta_multipla",
    "ASSERCAO_RAZAO": "submeter_assercao_razao",
}
ASSERTION_ANSWERS = {
    "A": "As asserções I e II são proposições verdadeiras, e a II é uma justificativa correta da I.",
    "B": "As asserções I e II são proposições verdadeiras, mas a II não é uma justificativa correta da I.",
    "C": "A asserção I é uma proposição verdadeira, e a II é uma proposição falsa.",
    "D": "A asserção I é uma proposição falsa, e a II é uma proposição verdadeira.",
    "E": "As asserções I e II são proposições falsas.",
}
HEADER_FIELDS = ("professor_nome", "professor_email", "uso_prova")
TOKEN_SALT = "questoes.submission.v1"


def restricted_period(componente):
    name = componente.periodo.nome.upper()
    return name in ("ESTÁGIO CURRICULAR OBRIGATÓRIO", "RESIDÊNCIA MÉDICA") or "SIMULADO" in name


def alternative_count(componente, style="RESPOSTA_UNICA"):
    if style == "MULTIPLA_ESCOLHA":
        return 5
    semestre = componente.periodo.semestre
    edital_2026_2 = semestre is not None and semestre.nome == "2026.2"
    return 4 if restricted_period(componente) or edital_2026_2 else 5


def allowed_styles(componente):
    return ("RESPOSTA_UNICA",) if restricted_period(componente) else tuple(FORM_ROUTES)


def new_submission_token(componente, style):
    return signing.dumps(
        {"id": str(uuid.uuid4()), "componente": componente.pk, "style": style}, salt=TOKEN_SALT
    )


def read_submission_token(token, componente, style):
    try:
        data = signing.loads(token, salt=TOKEN_SALT, max_age=86400)
        if data["componente"] not in componente.ids_compartilhados() or data["style"] != style:
            raise ValueError
        return uuid.UUID(data["id"])
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        raise ValidationError("O formulário expirou. Revise os dados e envie novamente.") from None


def validate_alternatives(alternatives, style, count):
    if not isinstance(alternatives, list):
        raise ValidationError("As alternativas devem formar uma lista válida.")
    minimum = 3 if style == "MULTIPLA_ESCOLHA" else count
    maximum = 5 if style == "MULTIPLA_ESCOLHA" else count
    if not minimum <= len(alternatives) <= maximum:
        raise ValidationError(f"Preencha {minimum} a {maximum} alternativas ou afirmativas.")
    texts = []
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            raise ValidationError("Alternativa inválida.")
        text = alternative.get("texto")
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValidationError("Cada alternativa deve conter texto de até 500 caracteres.")
        if type(alternative.get("eh_correta")) is not bool:
            raise ValidationError("Informe um gabarito válido para cada alternativa.")
        texts.append(text.strip().casefold())
    if len(set(texts)) != len(texts):
        raise ValidationError("As alternativas não podem ter textos repetidos.")
    correct = sum(alt["eh_correta"] for alt in alternatives)
    if style == "MULTIPLA_ESCOLHA":
        if not 0 < correct < len(alternatives):
            raise ValidationError("Marque pelo menos uma afirmativa correta e uma incorreta.")
    elif correct != 1:
        raise ValidationError("Marque exatamente uma alternativa correta.")


def validate_ai_draft(data, style, count):
    if not isinstance(data, dict):
        raise ValidationError("A IA retornou um rascunho inválido.")
    for field in ("texto_base", "enunciado", "justificativa"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValidationError("A IA retornou um rascunho incompleto.")
    if style == "ASSERCAO_RAZAO":
        if not isinstance(data.get("proposicao_dois"), str) or not data["proposicao_dois"].strip():
            raise ValidationError("A IA não preencheu a segunda proposição.")
        if data.get("assercao_gabarito") not in ASSERTION_ANSWERS:
            raise ValidationError("A IA não retornou um gabarito válido para as proposições.")
    else:
        validate_alternatives(data.get("alternativas"), style, count)
        if style == "MULTIPLA_ESCOLHA" and len(data["alternativas"]) > count:
            raise ValidationError("A IA ultrapassou a quantidade de campos disponíveis no formulário.")
    return data


@transaction.atomic
def save_submission(form, alternatives, token_id):
    # The unique receipt is inserted before the question: simultaneous/replayed
    # requests either reuse the committed receipt or fail without partial writes.
    receipt, created = SubmissionReceipt.objects.get_or_create(token=token_id)
    if not created:
        return receipt.questao, False
    # A form opened before consolidation may still contain a previous component ID.
    form.instance.componente = ComponenteCurricular.objects.get(pk=form.instance.componente_id).canonico
    question = form.save()
    Alternativa.objects.bulk_create(
        [Alternativa(questao=question, **alternative) for alternative in alternatives]
    )
    receipt.questao = question
    receipt.save(update_fields=["questao"])
    return question, True


def remember_header(request, question):
    key = str(uuid.uuid4())
    headers = request.session.get("submission_headers", {})
    # Keep separate continuation contexts for browser tabs, bounded per session.
    headers = dict(list(headers.items())[-19:])
    headers[key] = {field: getattr(question, field) for field in HEADER_FIELDS}
    headers[key]["componente"] = question.componente_id
    request.session["submission_headers"] = headers
    return key


def get_header(request, componente):
    key = request.GET.get("continuar", "")
    header = request.session.get("submission_headers", {}).get(key, {})
    if header.get("componente") not in componente.ids_compartilhados():
        return {}
    return {field: header[field] for field in HEADER_FIELDS}
