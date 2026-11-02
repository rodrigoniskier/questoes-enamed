"""Cartão de respostas legível em fotografia e identificador da versão da prova.

O identificador é uma impressão digital do gabarito e da ordem das questões;
não é um mecanismo de autenticação nem armazena dados de alunos.
"""

import hashlib
from math import ceil

LETRAS = "ABCDE"
MAXIMO_POR_FOLHA = 120


def _correta(alternativa):
    return alternativa["eh_correta"] if isinstance(alternativa, dict) else alternativa.eh_correta


def codigo_prova(questoes, alternativas_por_questao):
    """Código estável entre cartão, gabarito comentado e CSV."""
    partes = ["CARTAO-V1"]
    for numero, questao in enumerate(questoes, start=1):
        alternativas = alternativas_por_questao[questao.pk]
        corretas = [indice for indice, alternativa in enumerate(alternativas) if _correta(alternativa)]
        if len(corretas) != 1 or not 1 <= len(alternativas) <= len(LETRAS):
            raise ValueError(f"Questão {questao.pk}: alternativas ou gabarito inválidos.")
        partes.append(f"{numero}:{questao.pk}:{len(alternativas)}:{LETRAS[corretas[0]]}")
        for atributo in ("texto_base", "enunciado", "proposicao_dois"):
            partes.append(str(getattr(questao, atributo, "") or ""))
        for alternativa in alternativas:
            texto = alternativa["texto"] if isinstance(alternativa, dict) else alternativa.texto
            partes.append(str(texto))
    assinatura = hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest().upper()[:12]
    return f"{assinatura[:6]}-{assinatura[6:]}"


def paginas_do_cartao(questoes, alternativas_por_questao):
    """Cada questão aparece uma vez; até 120 em uma folha A4.

    Para mais de 120 questões, todas as folhas repetem o campo de identificação,
    o código da prova e o número da folha, evitando respostas órfãs.
    """
    itens = []
    for numero, questao in enumerate(questoes, start=1):
        alternativas = alternativas_por_questao[questao.pk]
        if len(alternativas) not in (4, 5):
            raise ValueError(f"Questão {questao.pk}: quantidade de alternativas inválida.")
        itens.append({"numero": numero, "letras": LETRAS[: len(alternativas)]})

    paginas = []
    total_paginas = ceil(len(itens) / MAXIMO_POR_FOLHA)
    for inicio in range(0, len(itens), MAXIMO_POR_FOLHA):
        lote = itens[inicio : inicio + MAXIMO_POR_FOLHA]
        numero_colunas = 2 if len(lote) <= 60 else 3
        itens_por_coluna = ceil(len(lote) / numero_colunas)
        colunas = [
            lote[i : i + itens_por_coluna]
            for i in range(0, len(lote), itens_por_coluna)
        ]
        paginas.append(
            {
                "numero": len(paginas) + 1,
                "total": total_paginas,
                "colunas": colunas,
                "numero_colunas": numero_colunas,
                "compacto": len(lote) > 60,
                "primeira": lote[0]["numero"],
                "ultima": lote[-1]["numero"],
            }
        )
    return paginas


def linhas_do_gabarito(questoes, alternativas_por_questao):
    """Dados objetivos para exportação em CSV e conferência de versões."""
    linhas = []
    for numero, questao in enumerate(questoes, start=1):
        alternativas = alternativas_por_questao[questao.pk]
        letra = next(
            LETRAS[indice]
            for indice, alternativa in enumerate(alternativas)
            if _correta(alternativa)
        )
        linhas.append(
            {
                "questao": numero,
                "questao_id": questao.pk,
                "quantidade_alternativas": len(alternativas),
                "correta": letra,
            }
        )
    return linhas
