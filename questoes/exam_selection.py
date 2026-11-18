"""Seleção de questões sem repetir o mesmo item, mesmo quando há IDs diferentes."""

import random
import re
import unicodedata


def _normalizar(texto):
    """Ignora variações de espaços, caixa e representação Unicode."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(texto or ""))).strip().casefold()


def identidade_questao(questao):
    """Identidade editorial: tipo, texto-base, comando e segunda proposição.

    O caminho da imagem separa questões com comandos iguais e imagens distintas.
    Duas cópias do mesmo texto em componentes diferentes usam a mesma identidade.
    """
    imagem = getattr(questao, "imagem", None)
    imagem_nome = getattr(imagem, "name", imagem) or ""
    return (
        _normalizar(questao.tipo_questao),
        _normalizar(questao.texto_base),
        _normalizar(questao.enunciado),
        _normalizar(questao.proposicao_dois),
        _normalizar(imagem_nome),
    )


def questoes_sao_unicas(questoes):
    identidades = [identidade_questao(questao) for questao in questoes]
    ids = [questao.pk for questao in questoes]
    return len(identidades) == len(set(identidades)) and len(ids) == len(set(ids))


def selecionar_questoes_unicas(bancos, quantidades):
    """Monta a maior seleção possível respeitando cotas e conteúdo único.

    'bancos' contém pares (ID de componente, questões elegíveis).
    Um emparelhamento por caminhos de aumento evita que um componente com
    mais opções monopolize uma questão necessária a outro componente.
    A ordem de saída continua agrupada pela ordem de entrada dos componentes.
    """
    candidatos = {}
    ordem = []
    for componente_id, questoes in bancos:
        ordem.append(componente_id)
        por_identidade = {}
        for questao in questoes:
            por_identidade.setdefault(identidade_questao(questao), questao)
        disponiveis = list(por_identidade.items())
        random.shuffle(disponiveis)
        candidatos[componente_id] = disponiveis

    # Os mais restritos tentam escolher antes; o remanejamento resolve conflitos.
    slots = [
        (componente_id, numero)
        for componente_id in sorted(ordem, key=lambda pk: (len(candidatos[pk]), ordem.index(pk)))
        for numero in range(quantidades[componente_id])
    ]
    donos = {}
    escolhidas = {}

    def encontrar_caminho(slot, vistas):
        componente_id, _ = slot
        for chave, questao in candidatos[componente_id]:
            if chave in vistas:
                continue
            vistas.add(chave)
            anterior = donos.get(chave)
            if anterior is None or encontrar_caminho(anterior, vistas):
                donos[chave] = slot
                escolhidas[slot] = questao
                return True
        return False

    for slot in slots:
        encontrar_caminho(slot, set())

    return [
        escolhidas[(componente_id, numero)]
        for componente_id in ordem
        for numero in range(quantidades[componente_id])
        if (componente_id, numero) in escolhidas
    ]
