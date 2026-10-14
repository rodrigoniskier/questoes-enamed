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

SEMANTIC_CRITERIA = {
    "02": "Clareza, precisão e necessidade das informações do texto-base.",
    "03": "Pertinência e qualidade de imagens, quando utilizadas.",
    "04": "Clareza, objetividade e precisão do comando.",
    "05": "Extensão e objetividade das opções de resposta.",
    "06": "Repetição indevida de termos do texto-base nas opções.",
    "07": "Organização lógica e padronização das opções.",
    "08": "Termos absolutos ou negativos que deem pistas por exclusão.",
    "09": "Plausibilidade pedagógica dos distratores, sem pegadinhas.",
}
UNCHECKABLE_CRITERIA = {
    "01": "Os objetivos de conhecimento da encomenda não estão persistidos para comparação.",
    "10": "A presença no acervo físico ou digital da Biblioteca do UNIPÊ não é consultável pelo sistema.",
    "11": "Autoria, confiabilidade, atualidade e formatação bibliográfica exigem conferência humana.",
    "12": "A declaração de material de autoria própria não possui campo estruturado na questão.",
    "13": "A classificação Bloom não possui campo estruturado na questão.",
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


def _achado(criterio, evidencia, fundamentacao, recomendacao, gravidade="obrigatoria", secao="3.3"):
    return {
        "criterio": criterio,
        "evidencia": evidencia,
        "fundamentacao": fundamentacao,
        "gravidade": gravidade,
        "recomendacao": recomendacao,
        "secao_manual": secao,
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
                "Inclua contexto pertinente ou confirme a regra institucional aplicável.",
            )
        )
    if not (questao.enunciado or "").strip():
        findings.append(
            _achado(
                "comando",
                "O enunciado está vazio.",
                "Não há comando para orientar a resposta.",
                "Redija um comando claro.",
            )
        )
    if not (questao.justificativa or "").strip():
        findings.append(
            _achado(
                "justificativa",
                "O campo justificativa está vazio.",
                "A justificativa é necessária para validar o gabarito e apoiar a revisão.",
                "Explique o gabarito e os distratores.",
                secao="3.4.1",
            )
        )

    if questao.imagem and snapshot_questao(questao)["imagem"].get("unreadable"):
        findings.append(
            _achado(
                "imagem.arquivo",
                "O arquivo de imagem vinculado não pode ser lido.",
                "O recurso declarado não está disponível para revisão ou exibição.",
                "Restaure ou substitua o arquivo de imagem antes de aprovar.",
                secao="3.1.1",
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
                    secao="3.4.1",
                )
            )
        if correct_count != 1:
            findings.append(
                _achado(
                    "resposta-unica.gabarito",
                    f"Foram marcadas {correct_count} respostas corretas.",
                    "Resposta única requer exatamente um gabarito.",
                    "Marque exatamente uma alternativa como correta.",
                    secao="3.3.9",
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
                    secao="3.4.1",
                )
            )
        if correct_count == 0 or correct_count == len(alternatives):
            findings.append(
                _achado(
                    "resposta-multipla.distribuicao",
                    f"Há {correct_count} afirmativas marcadas como corretas em {len(alternatives)}.",
                    "O conjunto precisa conter ao menos uma afirmativa verdadeira e uma falsa.",
                    "Revise a classificação de cada afirmativa.",
                    secao="3.3.10",
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
                    secao="3.4.1",
                )
            )
        if len(alternatives) != 5:
            findings.append(
                _achado(
                    "assercao-razao.chave",
                    f"Foram encontradas {len(alternatives)} opções de resposta.",
                    "A chave padronizada de asserção-razão utiliza cinco possibilidades.",
                    "Restaure as cinco possibilidades padronizadas.",
                    secao="3.4.1",
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
                    secao="3.2.3",
                )
            )
        if correct_count != 1:
            findings.append(
                _achado(
                    "assercao-razao.gabarito",
                    f"Foram marcadas {correct_count} opções como corretas.",
                    "A questão deve ter uma opção de gabarito.",
                    "Marque uma única possibilidade correta.",
                    secao="3.3.9",
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
    criteria = dict(SEMANTIC_CRITERIA)
    criteria["tipo"] = "Correspondência entre o tipo declarado e a estrutura efetivamente submetida."
    if questao.tipo_questao == "RESPOSTA_UNICA":
        criteria.update(
            {
                "15": "Paralelismo sintático e semântico das opções de resposta única.",
                "16": "Coesão, coerência e concordância entre comando e opções.",
            }
        )
    elif questao.tipo_questao == "MULTIPLA_ESCOLHA":
        criteria["17"] = "Independência e completude sintática das afirmações de resposta múltipla."
    elif questao.tipo_questao == "ASSERCAO_RAZAO":
        criteria.update(
            {
                "20": "Vínculo das proposições de asserção-razão com contexto e objetivos.",
                "21": "Presença de duas proposições simples e não excessivamente longas.",
                "22": "Possibilidade de analisar cada proposição independentemente.",
            }
        )
    criteria.pop("03", None)

    prompt = f"""
Você fará uma revisão pedagógica preliminar de uma questão médica. Não confirme correção clínica,
protocolo atual, origem bibliográfica, autoria ou acesso à biblioteca. Use exclusivamente o conteúdo
fornecido; não invente citações nem evidências. Responda somente JSON no schema:
{{"findings":[{{"criterion_id":"02","evidence":"trecho literal do conteúdo","reason":"...","recommendation":"...","severity":"recommendation"}}],
"inconclusive":[{{"criterion_id":"01","reason":"..."}}]}}

Para cada critério abaixo, registre exatamente uma ocorrência em findings ou inconclusive. Em findings,
evidence deve ser uma citação literal contida no conteúdo. Gravidade aceita: mandatory, recommendation,
conditional. Não trate recomendações como falha obrigatória. Se não houver evidência suficiente, marque
inconclusive. Se o tipo declarado não corresponder à estrutura, registre tipo como mandatory. A análise
não é validação clínica final.
Critérios: {json.dumps(criteria, ensure_ascii=False)}
Tipo declarado: {questao.get_tipo_questao_display()}
Conteúdo: {content}
"""

    if not settings.GEMINI_API_KEY:
        raise AIControlError("A IA está indisponível; não foi possível avaliar os critérios semânticos.")
    raw = generate_text(prompt, json_output=True, request_id=request_id)
    try:
        response = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("A resposta da IA não contém JSON válido.") from None

    if not isinstance(response, dict) or set(response) != {"findings", "inconclusive"}:
        raise ValueError("A resposta da IA não corresponde ao formato esperado.")
    if not isinstance(response["findings"], list) or not isinstance(response["inconclusive"], list):
        raise ValueError("A resposta da IA contém listas inválidas.")

    validated = []
    covered = set()
    valid_severities = {"mandatory", "recommendation", "conditional"}
    for finding in response["findings"]:
        if not isinstance(finding, dict):
            raise ValueError("A IA retornou um achado inválido.")
        criterion_id = finding.get("criterion_id")
        evidence = finding.get("evidence")
        reason = finding.get("reason")
        recommendation = finding.get("recommendation")
        severity = finding.get("severity")
        if (
            criterion_id not in criteria
            or criterion_id in covered
            or not all(
                isinstance(value, str) and value.strip() for value in (evidence, reason, recommendation)
            )
            or severity not in valid_severities
            or evidence.casefold() not in content.casefold()
        ):
            raise ValueError("A IA retornou achado sem evidência verificável ou fora do contrato.")
        covered.add(criterion_id)
        validated.append(
            _achado(
                f"manual.{criterion_id}",
                evidence[:500],
                reason[:1000],
                recommendation[:1000],
                severity,
                "Apêndice B",
            )
        )

    for item in response["inconclusive"]:
        if not isinstance(item, dict):
            raise ValueError("A IA retornou uma verificação inconclusiva inválida.")
        criterion_id = item.get("criterion_id")
        reason = item.get("reason")
        if (
            criterion_id not in criteria
            or criterion_id in covered
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("A IA retornou verificação inconclusiva fora do contrato.")
        covered.add(criterion_id)

    for criterion_id in criteria.keys() - covered:
        response["inconclusive"].append(
            {"criterion_id": criterion_id, "reason": "A resposta da IA não avaliou este critério."}
        )
    return validated, response["inconclusive"]


def executar_pre_auditoria(questao, revisor=None, semantica=True, request_id=None):
    questao = Questao.objects.select_related("componente__periodo__semestre").get(pk=questao.pk)
    findings = validar_estrutura(questao)
    uncheckable = dict(UNCHECKABLE_CRITERIA)
    if questao.tipo_questao == "MULTIPLA_ESCOLHA":
        uncheckable["19"] = (
            "O balanceamento da chave de combinações não está persistido no modelo da questão."
        )
    inconclusive = [{"criterion_id": key, "reason": reason} for key, reason in uncheckable.items()]
    if questao.imagem:
        inconclusive.append({"criterion_id": "03", "reason": "A imagem requer inspeção visual humana."})
    semantic_checked = False
    if semantica:
        try:
            semantic_findings, semantic_inconclusive = _semantic_review(questao, request_id=request_id)
            findings.extend(semantic_findings)
            inconclusive.extend(semantic_inconclusive)
            semantic_checked = True
        except (AIControlError, ValueError) as error:
            inconclusive.append({"criterion_id": "semantica", "reason": str(error)})
        except Exception as error:
            logger.error("Pre-audit semantic analysis failed: %s", type(error).__name__)
            inconclusive.append(
                {
                    "criterion_id": "semantica",
                    "reason": "A análise semântica falhou; nenhuma aprovação foi emitida.",
                }
            )
    else:
        inconclusive.append(
            {
                "criterion_id": "semantica",
                "reason": "A reavaliação foi estrutural; a análise semântica segue pendente.",
            }
        )

    result = (
        "PENDENCIAS"
        if any(finding["gravidade"] in ("obrigatoria", "mandatory") for finding in findings)
        else "INCONCLUSIVA"
    )
    indicators = {
        "estruturais_verificados": True,
        "semantica_verificada": semantic_checked,
        "criterios_aplicaveis_confirmados": False,
        "inconclusivos": inconclusive,
        "nota": "Há critérios que dependem de evidência institucional ou revisão humana; este parecer não aprova a questão.",
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
    from .models import EvidenciaRevisor

    current_fingerprint = fingerprint_questao(questao)
    if parecer.questao_id != questao.pk or parecer.fingerprint != current_fingerprint:
        raise ValidationError(
            "O parecer não corresponde à versão atual da questão. Reavalie antes de aprovar."
        )

    mandatory = [
        finding for finding in parecer.achados if finding.get("gravidade") in ("obrigatoria", "mandatory")
    ]
    if mandatory:
        raise ValidationError("Há pendências obrigatórias. Corrija a questão e execute nova pré-auditoria.")

    inconclusive = parecer.indicadores.get("inconclusivos", [])
    latest_evidence = {}
    for row in EvidenciaRevisor.objects.filter(parecer=parecer).order_by("criterio_id", "-criada_em", "-pk"):
        latest_evidence.setdefault(row.criterio_id, row)
    evidenced = set(latest_evidence)
    unresolved = [item for item in inconclusive if item.get("criterion_id") not in evidenced]
    if unresolved:
        criteria = ", ".join(str(item.get("criterion_id")) for item in unresolved)
        raise ValidationError(f"Registre conferência documental ou humana para os critérios: {criteria}.")
    documentary_ids = {"01", "10", "11", "12", "13"}
    missing_references = sorted(
        criterion_id
        for criterion_id, row in latest_evidence.items()
        if criterion_id in documentary_ids and len(row.referencia.strip()) < 8
    )
    if missing_references:
        criteria = ", ".join(missing_references)
        raise ValidationError(f"Informe a referência documental consultada para os critérios: {criteria}.")

    recommendations = [
        finding
        for finding in parecer.achados
        if finding.get("gravidade") in ("recomendacao", "recommendation", "condicional", "conditional")
    ]
    if recommendations and len(justificativa.strip()) < 15:
        raise ValidationError(
            "Justifique a aprovação excepcional das recomendações pedagógicas não impeditivas."
        )
    return current_fingerprint
