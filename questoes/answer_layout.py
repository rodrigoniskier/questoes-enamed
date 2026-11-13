"""Alternativas na ordem em que foram registradas no banco de questões."""

from collections import Counter

from .answer_sheet import LETRAS, linhas_do_gabarito
from .multiple_choice import gerar_alternativas_multipla_escolha


def alternativas_para_prova(questao):
    if questao.tipo_questao == "MULTIPLA_ESCOLHA":
        return gerar_alternativas_multipla_escolha(questao)
    return list(questao.alternativas.order_by("pk"))


def montar_alternativas(questoes):
    alternativas_por_questao = {}
    for questao in questoes:
        alternativas = alternativas_para_prova(questao)
        multiplas = questao.tipo_questao == "MULTIPLA_ESCOLHA"
        corretas = sum(alt["eh_correta"] if multiplas else alt.eh_correta for alt in alternativas)
        if multiplas and (len(alternativas) != 4 or corretas != 1):
            raise ValueError(f"Questão {questao.pk}: combinações de resposta múltipla inválidas.")
        if len(alternativas) not in (4, 5) or corretas != 1:
            raise ValueError(f"Questão {questao.pk}: alternativas ou gabarito inválidos.")
        alternativas_por_questao[questao.pk] = alternativas
    return alternativas_por_questao


def resumo_gabarito(questoes, alternativas_por_questao):
    contagens = Counter(linha["correta"] for linha in linhas_do_gabarito(questoes, alternativas_por_questao))
    total = len(questoes)
    return {
        "total": total,
        "distribuicao": [
            {
                "letra": letra,
                "quantidade": contagens[letra],
                "percentual": round(100 * contagens[letra] / total, 1) if total else 0.0,
            }
            for letra in LETRAS
            if letra != "E" or contagens[letra]
        ],
    }
