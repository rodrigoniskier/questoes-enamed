"""Comparações legíveis a partir das versões já preservadas na pré-auditoria."""

from .models import ComponenteCurricular, Questao
from .multiple_choice import linhas_combinacoes

FIELDS = (
    ("professor_nome", "Nome do professor"),
    ("professor_email", "E-mail do professor"),
    ("componente_id", "Componente curricular"),
    ("tipo_questao", "Tipo da questão"),
    ("uso_prova", "Destino da questão"),
    ("texto_base", "Texto-base"),
    ("imagem", "Imagem"),
    ("enunciado", "Enunciado / Proposição I"),
    ("proposicao_dois", "Proposição II"),
    ("justificativa", "Justificativa"),
    ("combinacoes_personalizadas", "Combinações finais (A–D)"),
    ("status", "Situação da questão"),
    ("comentario_validacao", "Feedback de validação"),
)


def orientacao_anterior(question):
    decisions = question.decisoes_pre_auditoria.filter(
        acao__in=["REPROVADA", "APROVADA_SUGESTOES", "CORRECAO"]
    ).exclude(justificativa="")
    decision = decisions.first()
    return decision.justificativa if decision else ""


def _display(field, value, snapshot, components):
    if field == "combinacoes_personalizadas":
        return linhas_combinacoes(value) if value else "Geradas automaticamente"
    if field == "componente_id":
        return snapshot.get("componente_nome") or components.get(value, f"Componente {value}")
    choices = {
        "tipo_questao": Questao.TIPO_QUESTAO_CHOICES,
        "uso_prova": Questao.USO_PROVA_CHOICES,
        "status": Questao.STATUS_CHOICES,
    }
    if field in choices:
        return dict(choices[field]).get(value, value)
    if field == "imagem" and isinstance(value, dict):
        return value.get("name", "Imagem registrada")
    return value or "Não preenchido"


def comparar_versoes(before, after, components=None):
    """Compare only fields actually known in both snapshots, including legacy ones."""
    components = components or {}
    rows = []
    for field, label in FIELDS:
        if field == "combinacoes_personalizadas" and (field in before or field in after):
            before = {field: [], **before}
            after = {field: [], **after}
        if field not in before or field not in after or (before[field] or "") == (after[field] or ""):
            continue
        old = _display(field, before[field], before, components)
        new = _display(field, after[field], after, components)
        if field == "imagem" and old == new:
            new = f"{new} (conteúdo do arquivo alterado)"
        rows.append({"campo": label, "antes": old, "depois": new})
    if "alternativas" in before and "alternativas" in after:
        old_alts, new_alts = before["alternativas"], after["alternativas"]
        for index in range(max(len(old_alts), len(new_alts))):
            old = old_alts[index]["texto"] if index < len(old_alts) else ""
            new = new_alts[index]["texto"] if index < len(new_alts) else ""
            if old != new:
                rows.append(
                    {
                        "campo": f"Alternativa {chr(65 + index)}",
                        "antes": old or "Alternativa ausente",
                        "depois": new or "Alternativa removida",
                    }
                )
        old_key = [chr(65 + i) for i, alt in enumerate(old_alts) if alt.get("eh_correta")]
        new_key = [chr(65 + i) for i, alt in enumerate(new_alts) if alt.get("eh_correta")]
        if old_key != new_key:
            rows.append(
                {
                    "campo": "Gabarito",
                    "antes": ", ".join(old_key) or "Não definido",
                    "depois": ", ".join(new_key) or "Não definido",
                }
            )
    return rows


def contexto_historico(question):
    changes = list(
        question.alteracoes_pre_auditoria.select_related("solicitacao_correcao").order_by("criada_em", "pk")
    )
    component_ids = {
        snapshot["componente_id"]
        for change in changes
        for snapshot in (change.antes, change.depois)
        if snapshot.get("componente_id")
    }
    components = {
        component.pk: str(component)
        for component in ComponenteCurricular.objects.filter(pk__in=component_ids).select_related("periodo")
    }
    # Uma única leitura impede consultas por registro e exclui pareceres posteriores a cada edição.
    decisions = list(
        question.decisoes_pre_auditoria.filter(
            acao__in=["REPROVADA", "APROVADA_SUGESTOES", "CORRECAO"]
        ).exclude(justificativa="")
    )
    entries = []
    for number, change in enumerate(changes, start=1):
        if "_orientacao_reenvio" in change.antes:
            guidance = change.antes["_orientacao_reenvio"]
        elif change.solicitacao_correcao_id:
            guidance = change.solicitacao_correcao.mensagem
        else:
            guidance = next(
                (d.justificativa for d in decisions if d.criada_em <= change.criada_em),
                change.antes.get("comentario_validacao", ""),
            )
        fields = comparar_versoes(change.antes, change.depois, components)
        entries.append(
            {
                "numero": number,
                "registro": change,
                "orientacao": guidance,
                "campos": fields,
                "conteudo_alterado": any(
                    row["campo"] not in {"Situação da questão", "Feedback de validação"} for row in fields
                ),
            }
        )
    return {
        "registros": list(reversed(entries)),
        "comparacao_original": (
            comparar_versoes(changes[0].antes, changes[-1].depois, components) if changes else []
        ),
        "primeiro_registro": changes[0] if changes else None,
        "ultimo_registro": changes[-1] if changes else None,
    }
