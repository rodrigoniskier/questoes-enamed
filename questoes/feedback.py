"""Vocabulário institucional das orientações, sem alterar conteúdo de questões."""

import re


def texto_pedagogico(texto):
    return re.sub(r"\breprovad[ao]s?\b", "Precisa de Revisões", str(texto or ""), flags=re.IGNORECASE)
