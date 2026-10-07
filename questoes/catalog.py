"""Consolida os blocos de turmas do edital sem separar bancos de questões."""

import json
import re
from pathlib import Path


def grade_compartilhada():
    path = Path(__file__).parent / "data" / "grade_2026_2.json"
    grade = json.loads(path.read_text(encoding="utf-8"))
    grupos = {}
    for bloco in grade["periodos"]:
        periodo = re.match(r"^([0-9]+º) Período", bloco["nome"])
        if not periodo:
            raise ValueError("Nome de período inválido no edital.")
        nome_periodo = periodo[1] + " Período"
        turmas = set(re.findall(r"\b[A-D]\b", bloco["nome"]))
        if not turmas:
            raise ValueError("Turmas ausentes no edital.")
        grupo = grupos.setdefault(nome_periodo, {"nome": nome_periodo, "blocos": [], "componentes": {}})
        grupo["blocos"].append(bloco["nome"])
        componentes = bloco["componentes"]
        if (
            len({c["nome"] for c in componentes}) != len(componentes)
            or any(
                type(c["numero_questoes_prova"]) is not int or c["numero_questoes_prova"] <= 0
                for c in componentes
            )
            or sum(c["numero_questoes_prova"] for c in componentes) != 50
        ):
            raise ValueError(
                "Cada bloco original deve totalizar 50 questões e não conter componentes duplicados."
            )
        for item in componentes:
            componente = grupo["componentes"].setdefault(
                item["nome"], {"base": item["nome"], "turmas": set(), "blocos": [], "meta": 0}
            )
            componente["turmas"].update(turmas)
            componente["blocos"].append(bloco["nome"])
            componente["meta"] = max(componente["meta"], item["numero_questoes_prova"])
    for grupo in grupos.values():
        for componente in grupo["componentes"].values():
            turmas = sorted(componente["turmas"])
            label = turmas[0] if len(turmas) == 1 else ", ".join(turmas[:-1]) + " e " + turmas[-1]
            prefix = "turma" if len(turmas) == 1 else "turmas"
            componente["nome"] = f"{componente['base']} ({prefix} {label})"
    if (
        grade["semestre"] != "2026.2"
        or len(grupos) != 5
        or sum(len(g["componentes"]) for g in grupos.values()) != 50
    ):
        raise ValueError("A consolidação deve conter cinco períodos e 50 componentes.")
    return grade, list(grupos.values())
