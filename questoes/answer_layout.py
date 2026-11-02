"""Distribuição de gabaritos A–D sem violar a ordem por extensão."""

import re
from itertools import combinations


class LayoutBalanceError(ValueError):
    """A montagem não satisfaz simultaneamente as regras da prova."""


def _extensao(texto):
    sem_rotulo = re.sub(r"^[A-Ea-e][).\-]\s*", "", texto or "")
    return len(" ".join(sem_rotulo.split()))


def _opcoes_resposta_unica(questao):
    alternativas = list(questao.alternativas.all().order_by("id"))
    if len(alternativas) not in (4, 5) or sum(a.eh_correta for a in alternativas) != 1:
        raise LayoutBalanceError(f"Questão {questao.pk}: quantidade ou gabarito de resposta única inválido.")

    # Bancos antigos de cinco alternativas permanecem utilizáveis, mas não
    # participam do balanceamento A–D, reservado a itens de quatro opções.
    if len(alternativas) == 5:
        return {}, sorted(alternativas, key=lambda a: (_extensao(a.texto), a.pk))

    correta = next(a for a in alternativas if a.eh_correta)
    opcoes = {}
    for reversa in (False, True):
        sinal = -1 if reversa else 1
        ordenadas = sorted(alternativas, key=lambda a: (sinal * _extensao(a.texto), a.pk))
        indice_correto = ordenadas.index(correta)
        # Empates de extensão admitem trocas sem quebrar a monotonicidade.
        for indice, alternativa in enumerate(ordenadas):
            if _extensao(alternativa.texto) != _extensao(correta.texto):
                continue
            copia = ordenadas.copy()
            copia[indice_correto], copia[indice] = copia[indice], copia[indice_correto]
            opcoes.setdefault(indice, copia)
    return opcoes, None


def _opcoes_resposta_multipla(questao, gerar_multipla):
    alternativas = gerar_multipla(questao)
    if len(alternativas) != 4 or sum(a["eh_correta"] for a in alternativas) != 1:
        raise LayoutBalanceError(f"Questão {questao.pk}: combinações de resposta múltipla inválidas.")
    indice_correto = next(i for i, a in enumerate(alternativas) if a["eh_correta"])
    opcoes = {}
    for indice in range(4):
        deslocamento = (indice - indice_correto) % 4
        opcoes[indice] = (
            alternativas[-deslocamento:] + alternativas[:-deslocamento]
            if deslocamento
            else alternativas.copy()
        )
    return opcoes


def _encaixar(questoes_por_id, capacidades):
    """Emparelha cada questão a um slot A/B/C/D, sem aproximações."""
    slots = [letra for letra, capacidade in enumerate(capacidades) for _ in range(capacidade)]
    ocupantes = [None] * len(slots)
    indices = {
        questao_id: [indice for indice, letra in enumerate(slots) if letra in alternativas]
        for questao_id, alternativas in questoes_por_id.items()
    }

    def tentar(questao_id, visitados):
        for indice in indices[questao_id]:
            if indice in visitados:
                continue
            visitados.add(indice)
            ocupante = ocupantes[indice]
            if ocupante is None or tentar(ocupante, visitados):
                ocupantes[indice] = questao_id
                return True
        return False

    # Itens com menos posições possíveis são tratados primeiro.
    ordem = sorted(questoes_por_id, key=lambda q: (len(questoes_por_id[q]), q))
    for questao_id in ordem:
        if not tentar(questao_id, set()):
            return None
    return {questao_id: slots[i] for i, questao_id in enumerate(ocupantes)}


def montar_layout_balanceado(questoes, gerar_multipla):
    """Retorna alternativas finais por ID, com contagens A–D diferindo até um.

    Respostas únicas são ordenadas por extensão crescente ou decrescente.
    Respostas múltiplas podem ocupar qualquer das quatro letras. Asserção-razão
    (cinco opções) e provas legadas de cinco alternativas são preservadas fora
    do cálculo A–D. Se o balanceamento for impossível, a prova não é gerada.
    """
    fixas = {}
    candidatas = {}
    for questao in questoes:
        if questao.tipo_questao == "RESPOSTA_UNICA":
            opcoes, legada = _opcoes_resposta_unica(questao)
            if legada is not None:
                fixas[questao.pk] = legada
            else:
                candidatas[questao.pk] = opcoes
        elif questao.tipo_questao == "MULTIPLA_ESCOLHA":
            candidatas[questao.pk] = _opcoes_resposta_multipla(questao, gerar_multipla)
        else:
            fixas[questao.pk] = list(questao.alternativas.all().order_by("id"))

    quantidade = len(candidatas)
    minimo, sobra = divmod(quantidade, 4)
    for letras_extras in combinations(range(4), sobra):
        capacidades = [minimo + (1 if letra in letras_extras else 0) for letra in range(4)]
        atribuicoes = _encaixar(candidatas, capacidades)
        if atribuicoes is not None:
            return {
                **fixas,
                **{questao_id: candidatas[questao_id][letra] for questao_id, letra in atribuicoes.items()},
            }

    raise LayoutBalanceError(
        "Não é possível equilibrar A, B, C e D mantendo todas as alternativas "
        "em ordem crescente ou decrescente de extensão. Selecione outra "
        "combinação de questões ou revise a extensão das alternativas."
    )
