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
    """Ordena respostas únicas por extensão e busca equilíbrio A–D quando viável.

    Se as restrições impedirem equilíbrio exato, conserva a ordenação por
    extensão e distribui as letras disponíveis, sem bloquear a prova.
    Questões de cinco alternativas ficam fora do objetivo A–D.
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

    # Prioridade pedagógica: jamais quebrar a monotonicidade das respostas
    # únicas para forçar letras do gabarito. Distribui as posições possíveis.
    atribuicoes = {}
    frequencias = [0, 0, 0, 0]
    ordem = sorted(candidatas, key=lambda pk: (len(candidatas[pk]), pk))
    for questao_id in ordem:
        letra = min(candidatas[questao_id], key=lambda indice: (frequencias[indice], indice))
        atribuicoes[questao_id] = letra
        frequencias[letra] += 1
    return {
        **fixas,
        **{questao_id: candidatas[questao_id][letra] for questao_id, letra in atribuicoes.items()},
    }


def resumo_gabarito(questoes, alternativas_por_questao):
    """Contagens e percentuais reais, sem ocultar itens A–E.

    'equilibrado' verifica somente itens com quatro alternativas, como a
    política atual do gerador. As proporções exibidas usam o total da prova.
    """
    contagens = {letra: 0 for letra in "ABCDE"}
    contagens_quatro = {letra: 0 for letra in "ABCD"}
    for questao in questoes:
        alternativas = alternativas_por_questao[questao.pk]
        indices = [
            indice
            for indice, alternativa in enumerate(alternativas)
            if (alternativa["eh_correta"] if isinstance(alternativa, dict) else alternativa.eh_correta)
        ]
        if len(indices) != 1 or indices[0] >= len("ABCDE"):
            raise LayoutBalanceError(f"Questão {questao.pk}: gabarito inválido.")
        letra = "ABCDE"[indices[0]]
        contagens[letra] += 1
        if len(alternativas) == 4:
            contagens_quatro[letra] += 1

    total = len(questoes)
    equilibrado = max(contagens_quatro.values()) - min(contagens_quatro.values()) <= 1
    distribuicao = [
        {
            "letra": letra,
            "quantidade": quantidade,
            "percentual": round(100 * quantidade / total, 1) if total else 0.0,
        }
        for letra, quantidade in contagens.items()
        if letra != "E" or quantidade
    ]
    return {
        "total": total,
        "equilibrado": equilibrado,
        "quatro_alternativas": sum(contagens_quatro.values()),
        "cinco_alternativas": total - sum(contagens_quatro.values()),
        "distribuicao": distribuicao,
        "contagens_quatro": contagens_quatro,
    }
