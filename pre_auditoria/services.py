import hashlib
import json
import logging
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError

from questoes.ai_control import AIControlError, generate_text
from questoes.models import Questao
from questoes.submission import ASSERTION_ANSWERS, alternative_count

logger = logging.getLogger(__name__)
DIRECT_IDENTIFIER_PATTERNS = (
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\(?\d{2}\)?\s*9?\d{4}[-\s]\d{4}\b"),
    re.compile(
        r"(?i)\bpaciente\s*[:,-]\s*[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+){0,2}\b"
    ),
)

POLICY_VERSION = "submeter-basica-v1"
SEMANTIC_CRITERIA = {
    "texto_base": "Clareza, objetividade e contextualização básica do texto-base.",
    "enunciado": "Clareza do enunciado e ausência de formulações negativas desnecessárias.",
    "alternativas": "Organização, tamanho, paralelismo, termos absolutos e plausibilidade aparente das alternativas.",
    "afirmativas": "Independência e clareza das afirmativas de resposta múltipla.",
    "proposicoes": "Clareza e concisão das proposições de asserção-razão.",
    "justificativa": "Coerência aparente da justificativa com o gabarito informado.",
}


def snapshot_questao(questao):
    image = ""
    if questao.imagem:
        digest = hashlib.sha256()
        try:
            with questao.imagem.open("rb") as image_file:
                for chunk in image_file.chunks():
                    digest.update(chunk)
            image = {"name": questao.imagem.name, "sha256": digest.hexdigest()}
        except (OSError, ValueError):
            image = {"name": questao.imagem.name, "unreadable": True}
    return {
        "tipo_questao": questao.tipo_questao,
        "uso_prova": questao.uso_prova,
        "texto_base": questao.texto_base or "",
        "enunciado": questao.enunciado or "",
        "proposicao_dois": questao.proposicao_dois or "",
        "justificativa": questao.justificativa or "",
        "comentario_validacao": questao.comentario_validacao or "",
        "imagem": image,
        "alternativas": [
            {"texto": alternativa.texto, "eh_correta": alternativa.eh_correta}
            for alternativa in questao.alternativas.order_by("pk")
        ],
    }


def fingerprint_questao(questao):
    serialized = json.dumps(snapshot_questao(questao), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _achado(criterio, evidencia, fundamentacao, recomendacao, gravidade="obrigatoria"):
    return {
        "criterio": criterio,
        "evidencia": evidencia,
        "fundamentacao": fundamentacao,
        "gravidade": gravidade,
        "recomendacao": recomendacao,
        "validacao": "deterministica",
    }


def validar_estrutura(questao):
    alternatives = list(questao.alternativas.order_by("pk"))
    findings = []

    if not (questao.texto_base or "").strip():
        findings.append(
            _achado(
                "texto-base",
                "O campo texto-base está vazio.",
                "O formulário exige texto-base para contextualizar o item.",
                "Preencha o texto-base com o contexto da questão.",
            )
        )
    if not (questao.enunciado or "").strip():
        findings.append(
            _achado(
                "comando",
                "O enunciado está vazio.",
                "Não há comando para orientar a resposta.",
                "Preencha o enunciado com a pergunta ou comando da questão.",
            )
        )
    if not (questao.justificativa or "").strip():
        findings.append(
            _achado(
                "justificativa",
                "O campo justificativa está vazio.",
                "A justificativa é necessária para validar o gabarito e apoiar a revisão.",
                "Preencha a justificativa do gabarito.",
            )
        )

    if any(not (alternative.texto or "").strip() for alternative in alternatives):
        findings.append(
            _achado(
                "alternativas",
                "Existe alternativa sem texto.",
                "As opções devem estar completas.",
                "Preencha ou remova a opção vazia.",
            )
        )

    texts = [
        alternative.texto.strip().casefold() for alternative in alternatives if alternative.texto.strip()
    ]
    if len(texts) != len(set(texts)):
        findings.append(
            _achado(
                "alternativas",
                "Há textos de alternativas repetidos.",
                "Opções duplicadas comprometem a discriminação do item.",
                "Revise as opções repetidas.",
            )
        )

    correct_count = sum(alternative.eh_correta for alternative in alternatives)
    if questao.tipo_questao == "RESPOSTA_UNICA":
        expected_count = alternative_count(questao.componente)
        if len(alternatives) != expected_count:
            findings.append(
                _achado(
                    "resposta-unica.quantidade",
                    f"Foram encontradas {len(alternatives)} alternativas; a política do componente exige {expected_count}.",
                    "A quantidade segue a regra efetivamente usada pelo semestre e não impõe retroativamente a regra geral de cinco opções.",
                    "Ajuste a quantidade conforme o semestre do componente.",
                )
            )
        if correct_count != 1:
            findings.append(
                _achado(
                    "resposta-unica.gabarito",
                    f"Foram marcadas {correct_count} respostas corretas.",
                    "Resposta única requer exatamente um gabarito.",
                    "Marque exatamente uma alternativa como correta.",
                )
            )
    elif questao.tipo_questao == "MULTIPLA_ESCOLHA":
        if not 3 <= len(alternatives) <= 5:
            findings.append(
                _achado(
                    "resposta-multipla.quantidade",
                    f"Foram encontradas {len(alternatives)} afirmativas.",
                    "Resposta múltipla requer de três a cinco afirmativas.",
                    "Mantenha entre três e cinco afirmativas independentes.",
                )
            )
        if correct_count == 0 or correct_count == len(alternatives):
            findings.append(
                _achado(
                    "resposta-multipla.distribuicao",
                    f"Há {correct_count} afirmativas marcadas como corretas em {len(alternatives)}.",
                    "O conjunto precisa conter ao menos uma afirmativa verdadeira e uma falsa.",
                    "Revise a classificação de cada afirmativa.",
                )
            )
    elif questao.tipo_questao == "ASSERCAO_RAZAO":
        if not (questao.proposicao_dois or "").strip():
            findings.append(
                _achado(
                    "assercao-razao.proposicao",
                    "A proposição II está vazia.",
                    "O tipo declarado requer duas proposições.",
                    "Inclua a proposição II.",
                )
            )
        if len(alternatives) != 5:
            findings.append(
                _achado(
                    "assercao-razao.chave",
                    f"Foram encontradas {len(alternatives)} opções de resposta.",
                    "A chave padronizada de asserção-razão utiliza cinco possibilidades.",
                    "Restaure as cinco possibilidades padronizadas.",
                )
            )
        elif [alternative.texto.strip().casefold() for alternative in alternatives] != [
            f"{letter}) {text}".casefold() for letter, text in ASSERTION_ANSWERS.items()
        ]:
            findings.append(
                _achado(
                    "assercao-razao.chave-padrao",
                    "As cinco opções não correspondem à chave padronizada A–E.",
                    "A asserção-razão deve oferecer as cinco combinações canônicas de veracidade e causalidade.",
                    "Use a chave padronizada do sistema para as cinco possibilidades.",
                )
            )
        if correct_count != 1:
            findings.append(
                _achado(
                    "assercao-razao.gabarito",
                    f"Foram marcadas {correct_count} opções como corretas.",
                    "A questão deve ter uma opção de gabarito.",
                    "Marque uma única possibilidade correta.",
                )
            )
    else:
        findings.append(
            _achado(
                "tipo",
                f"Tipo declarado não reconhecido: {questao.tipo_questao!r}.",
                "O tipo declarado não corresponde aos formatos suportados.",
                "Corrija o tipo da questão.",
            )
        )

    return findings


def _semantic_review(questao, request_id=None):
    snapshot = snapshot_questao(questao)
    content = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    if any(pattern.search(content) for pattern in DIRECT_IDENTIFIER_PATTERNS):
        raise AIControlError(
            "O conteúdo pode conter identificadores pessoais; a chamada externa foi bloqueada para conferência humana."
        )
    criteria = {
        "texto_base": SEMANTIC_CRITERIA["texto_base"],
        "justificativa": SEMANTIC_CRITERIA["justificativa"],
    }
    if questao.tipo_questao == "RESPOSTA_UNICA":
        criteria.update(
            enunciado=SEMANTIC_CRITERIA["enunciado"], alternativas=SEMANTIC_CRITERIA["alternativas"]
        )
    elif questao.tipo_questao == "MULTIPLA_ESCOLHA":
        criteria.update(
            afirmativas=SEMANTIC_CRITERIA["afirmativas"], alternativas=SEMANTIC_CRITERIA["alternativas"]
        )
    elif questao.tipo_questao == "ASSERCAO_RAZAO":
        criteria["proposicoes"] = SEMANTIC_CRITERIA["proposicoes"]

    prompt = f"""
    Você é um assistente opcional de revisão textual preliminar de uma questão médica. Observe somente
    os critérios listados abaixo. Não avalie correção médica, protocolos, bibliografia, autoria, Bloom,
    currículo ou objetivos de aprendizagem. Não aprove nem reprove a questão. Responda somente JSON no
    formato {{"recommendations":[{{"criterion_id":"texto_base","evidence":"trecho literal",
    "reason":"...","recommendation":"..."}}]}}. A lista pode ser vazia; inclua somente pontos
    de atenção concretos, com trecho literal existente no conteúdo. Não invente informações ou citações.
    Critérios: {json.dumps(criteria, ensure_ascii=False)}
    Tipo: {questao.get_tipo_questao_display()}
    Conteúdo: {content}
"""

    if not settings.GEMINI_API_KEY:
        raise AIControlError("A IA está indisponível; não foi possível avaliar os critérios semânticos.")
    raw = generate_text(prompt, json_output=True, request_id=request_id)
    try:
        response = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("A resposta da IA não contém JSON válido.") from None

    if not isinstance(response, dict) or set(response) != {"recommendations"}:
        raise ValueError("A resposta da IA não corresponde ao formato esperado.")
    if not isinstance(response["recommendations"], list):
        raise ValueError("A resposta da IA contém recomendações inválidas.")

    validated = []
    covered = set()
    for finding in response["recommendations"]:
        if not isinstance(finding, dict) or set(finding) != {
            "criterion_id",
            "evidence",
            "reason",
            "recommendation",
        }:
            raise ValueError("A IA retornou um achado inválido.")
        criterion_id = finding.get("criterion_id")
        evidence = finding.get("evidence")
        reason = finding.get("reason")
        recommendation = finding.get("recommendation")
        if (
            criterion_id not in criteria
            or criterion_id in covered
            or not all(
                isinstance(value, str) and value.strip() for value in (evidence, reason, recommendation)
            )
            or evidence.casefold() not in content.casefold()
        ):
            raise ValueError("A IA retornou achado sem evidência verificável ou fora do contrato.")
        covered.add(criterion_id)
        validated.append(
            _achado(
                f"recomendacao.{criterion_id}",
                evidence[:500],
                reason[:1000],
                recommendation[:1000],
                "recomendacao",
            )
        )
    return validated


def executar_pre_auditoria(questao, revisor=None, semantica=False, request_id=None):
    questao = Questao.objects.select_related("componente__periodo__semestre").get(pk=questao.pk)
    findings = validar_estrutura(questao)
    semantic_checked = False
    semantic_warning = ""
    if semantica and not findings:
        try:
            findings.extend(_semantic_review(questao, request_id=request_id))
            semantic_checked = True
        except (AIControlError, ValueError) as error:
            semantic_warning = str(error)
        except Exception as error:
            logger.error("Pre-audit semantic analysis failed: %s", type(error).__name__)
            semantic_warning = "A análise textual está indisponível. A revisão estrutural foi concluída."

    result = (
        "PENDENCIAS"
        if any(finding["gravidade"] in ("obrigatoria", "mandatory") for finding in findings)
        else "APROVADA"
    )
    finding_ids = {finding["criterio"] for finding in findings}
    indicators = {
        "politica": POLICY_VERSION,
        "estruturais_verificados": True,
        "semantica_verificada": semantic_checked,
        "analise_semantica_aviso": semantic_warning,
        "tem_recomendacoes": any(finding["gravidade"] == "recomendacao" for finding in findings),
        "validacoes_estruturais": [
            {"criterio": label, "conforme": not any(key in finding_ids for key in keys)}
            for label, keys in (
                ("Texto-base preenchido", ("texto-base",)),
                ("Enunciado ou proposições preenchidos", ("comando", "assercao-razao.proposicao")),
                ("Alternativas ou afirmativas completas e sem duplicação", ("alternativas",)),
                (
                    "Quantidade conforme o tipo",
                    (
                        "resposta-unica.quantidade",
                        "resposta-multipla.quantidade",
                        "assercao-razao.chave",
                        "assercao-razao.chave-padrao",
                    ),
                ),
                (
                    "Gabarito válido",
                    ("resposta-unica.gabarito", "resposta-multipla.distribuicao", "assercao-razao.gabarito"),
                ),
                ("Justificativa preenchida", ("justificativa",)),
                ("Estrutura compatível com o tipo", ("tipo",)),
            )
        ],
        "nota": "A pré-auditoria verifica a estrutura; a decisão técnica, científica e pedagógica é do gestor.",
    }

    from .models import ParecerPreAuditoria

    defaults = {
        "questao": questao,
        "resultado": result,
        "fingerprint": fingerprint_questao(questao),
        "achados": findings,
        "indicadores": indicators,
        "revisor": revisor,
        "revisor_nome": revisor.get_username() if revisor else "",
    }
    if request_id:
        report, _ = ParecerPreAuditoria.objects.get_or_create(
            request_id=uuid.UUID(str(request_id)), defaults=defaults
        )
        if report.questao_id != questao.pk or report.fingerprint != defaults["fingerprint"]:
            raise ValidationError("A chave idempotente já está vinculada a outra questão ou versão.")
        return report
    return ParecerPreAuditoria.objects.create(**defaults)


def validar_aprovacao(questao, parecer, justificativa=""):
    current_fingerprint = fingerprint_questao(questao)
    if parecer.questao_id != questao.pk or parecer.fingerprint != current_fingerprint:
        raise ValidationError(
            "O parecer não corresponde à versão atual da questão. Reavalie antes de aprovar."
        )
    if parecer.indicadores.get("politica") != POLICY_VERSION:
        raise ValidationError("Este parecer é histórico. Execute uma nova pré-auditoria antes de aprovar.")

    mandatory = [
        finding for finding in parecer.achados if finding.get("gravidade") in ("obrigatoria", "mandatory")
    ]
    if mandatory:
        raise ValidationError("Há pendências obrigatórias. Corrija a questão e execute nova pré-auditoria.")

    return current_fingerprint
