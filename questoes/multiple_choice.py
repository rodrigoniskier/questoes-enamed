"""Combinações de afirmativas sem pistas baseadas em uma única afirmativa."""

import random
from functools import lru_cache
from itertools import combinations

from django.core.exceptions import ValidationError

ROMANOS = ("I", "II", "III", "IV", "V")


def formatar_combinacao(indices):
    romanos = [ROMANOS[i - 1] for i in indices]
    if len(romanos) == 1:
        return f"Apenas a afirmativa {romanos[0]} está correta."
    return f"Apenas as afirmativas {', '.join(romanos[:-1])} e {romanos[-1]} estão corretas."


def validar_combinacoes(combinacoes, afirmativas):
    """Valida a configuração manual também contra as afirmativas alteradas no mesmo POST."""
    quantidade = len(afirmativas)
    corretas = [i + 1 for i, afirmativa in enumerate(afirmativas) if afirmativa["eh_correta"]]
    if not 3 <= quantidade <= 5 or not 0 < len(corretas) < quantidade:
        raise ValidationError("Preencha de 3 a 5 afirmativas, com pelo menos uma correta e uma incorreta.")
    if not isinstance(combinacoes, list) or len(combinacoes) != 4:
        raise ValidationError("Informe exatamente quatro combinações, uma por linha (A, B, C e D).")
    for indices in combinacoes:
        if (
            not isinstance(indices, list)
            or not indices
            or any(type(i) is not int or not 1 <= i <= quantidade for i in indices)
            or indices != sorted(set(indices))
        ):
            raise ValidationError("Cada combinação deve usar afirmativas existentes, sem repetições.")
    if len({tuple(indices) for indices in combinacoes}) != 4:
        raise ValidationError("As quatro combinações devem ser diferentes.")
    if sum(indices == corretas for indices in combinacoes) != 1:
        raise ValidationError("Uma combinação deve corresponder exatamente a todas as afirmativas corretas.")
    if any(sum(i in indices for indices in combinacoes) != 2 for i in range(1, quantidade + 1)):
        raise ValidationError(
            "Para evitar pistas, cada afirmativa deve aparecer em duas combinações e ficar ausente em duas."
        )


def linhas_combinacoes(combinacoes):
    return "\n".join(
        f"{letter}) {', '.join(ROMANOS[i - 1] for i in indices)}"
        for letter, indices in zip("ABCD", combinacoes)
    )


def interpretar_combinacoes(value):
    import re

    if not value.strip():
        return []
    result = []
    for line in value.strip().splitlines():
        line = re.sub(r"^[A-D][):.\-]\s*", "", line.strip().upper())
        tokens = re.split(r"\s*[,;]\s*|\s+E\s+", line)
        if any(token not in ROMANOS for token in tokens) or len(set(tokens)) != len(tokens):
            raise ValidationError("Use algarismos romanos separados por vírgulas, por exemplo: I, II, IV.")
        result.append(sorted(ROMANOS.index(token) + 1 for token in tokens))
    return result


@lru_cache(maxsize=64)
def _distratores_balanceados(quantidade, corretas):
    """Em quatro opções, cada afirmativa deve aparecer duas vezes e faltar duas."""
    possibilidades = [mask for mask in range(1, 1 << quantidade) if mask != corretas]
    conjuntos = []
    for distratores in combinations(possibilidades, 3):
        opcoes = (corretas, *distratores)
        if all(sum(bool(mask & (1 << i)) for mask in opcoes) == 2 for i in range(quantidade)):
            conjuntos.append(distratores)
    return tuple(conjuntos)


def gerar_alternativas_multipla_escolha(questao):
    afirmativas = list(questao.alternativas.all().order_by("id"))
    quantidade = len(afirmativas)
    corretas = sum(1 << i for i, afirmativa in enumerate(afirmativas) if afirmativa.eh_correta)
    if not 3 <= quantidade <= 5 or corretas in (0, (1 << quantidade) - 1):
        return [{"texto": "Questão com afirmativas inválidas: revise no banco.", "eh_correta": False}]

    # Só o padrão de verdadeiro/falso entra no cache; nenhum texto ou dado pessoal.
    combinacoes = questao.combinacoes_personalizadas
    if combinacoes:
        try:
            validar_combinacoes(combinacoes, [{"eh_correta": a.eh_correta} for a in afirmativas])
        except ValidationError:
            return [{"texto": "Combinações personalizadas inválidas: revise no banco.", "eh_correta": False}]
    else:
        conjuntos = _distratores_balanceados(quantidade, corretas)
        rng = random.Random(questao.pk)
        masks = [corretas, *rng.choice(conjuntos)]
        rng.shuffle(masks)
        combinacoes = [[i + 1 for i in range(quantidade) if mask & (1 << i)] for mask in masks]
    return [
        {
            "texto": formatar_combinacao(indices),
            "eh_correta": sum(1 << (i - 1) for i in indices) == corretas,
            "indices": indices,
        }
        for indices in combinacoes
    ]
