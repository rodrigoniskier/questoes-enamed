"""Comandos exibidos na prova, sem alterar o conteúdo cadastrado."""

import re
import unicodedata

from .submission import MULTIPLE_STATEMENT_PROMPT


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r"\s+", " ", texto).strip().rstrip(" .:;!?")


def _comando_generico(comando):
    # Aceita somente instruções genéricas completas. Complementos de tema,
    # negação ou tarefas adicionais continuam sendo comandos personalizados.
    return re.fullmatch(
        r"(?:(?:considerando o|com base no) texto(?:[- ]base| apresentado)?,? )?"
        r"(?:avalie|analise|julgue) as (?:afirmacoes|afirmativas|assertivas)"
        r"(?: a seguir| abaixo)?",
        comando,
    )


def enunciado_para_prova(questao):
    enunciado = (questao.enunciado or "").strip()
    if questao.tipo_questao != "MULTIPLA_ESCOLHA":
        return enunciado

    enunciado = enunciado or MULTIPLE_STATEMENT_PROMPT
    texto_base = (questao.texto_base or "").strip()
    # Considera a última instrução, evitando confundir o relato do caso com
    # um comando dirigido ao aluno. Não remove instruções personalizadas
    # diferentes, que podem delimitar o tema ou pedir afirmativas incorretas.
    trechos = re.split(r"(?<=[.!?])\s+|[\r\n]+", texto_base)
    ultimo_trecho = _normalizar(next((trecho for trecho in reversed(trechos) if trecho.strip()), ""))
    # Uma quebra de linha também pode ser apenas a continuação da mesma
    # instrução (por exemplo, texto copiado de um PDF).
    frases = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", texto_base))
    ultima_frase = _normalizar(frases[-1])
    comando = _normalizar(enunciado)
    if comando in (ultimo_trecho, ultima_frase):
        return ""
    if not _comando_generico(comando):
        return enunciado

    instrucao = re.compile(
        r"(?:^|[,;:]\s*)(?:avalie|analise|julgue|examine|considere|verifique)\b"
        r"[^.!?]*\b(?:afirmacoes|afirmativas|assertivas|assercoes|proposicoes|itens)\b"
        r"|(?:^|[,;:]\s*|\be\s+)(?:assinale|marque|selecione|indique)\b"
        r"[^.!?]*\b(?:alternativa|opcao|afirmativas|afirmacoes|assertivas|itens)\b"
    )
    return "" if any(instrucao.search(trecho) for trecho in (ultimo_trecho, ultima_frase)) else enunciado
