# Em: bulk_submit/utils.py

import json
import re  # Para ajudar a limpar a resposta da IA

from django.core.exceptions import ValidationError

from questoes.ai_control import AIControlError, generate_text
from questoes.models import Questao
from questoes.submission import allowed_styles, alternative_count, validate_ai_draft


def processar_texto_com_ia(texto_bruto, request_id=None):
    """
    Envia o texto bruto contendo múltiplas questões para a API do Gemini
    e tenta retornar uma lista de dicionários, cada um representando uma questão.
    """
    try:
        # --- PROMPT DE PARSING (IMPORTANTE AJUSTAR/TESTAR) ---
        # Este prompt é um ponto de partida. Pode precisar de muitos ajustes
        # dependendo da variabilidade do texto que os professores colarem.
        prompt = f"""
        Sua tarefa é analisar o TEXTO FORNECIDO abaixo, que contém uma ou mais questões de múltipla escolha para um curso de medicina.
        Identifique CADA questão individualmente no texto. Para CADA questão, extraia as seguintes informações:

        1.  `tipo_questao`: Identifique o tipo. Use "RESPOSTA_UNICA" (se tem A, B, C, D, E e só uma é correta), "MULTIPLA_ESCOLHA" (se tem afirmativas I, II, III e depois A, B, C, D, E combinando-as), ou "ASSERCAO_RAZAO" (se tem duas proposições I e II ligadas por PORQUE). Se não conseguir identificar, use "RESPOSTA_UNICA" como padrão.
        2.  `texto_base`: O texto introdutório, caso clínico ou contexto antes do enunciado. Pode ser nulo.
        3.  `enunciado`: A pergunta principal ou a Proposição I (para Asserção-Razão).
        4.  `proposicao_dois`: A Proposição II (apenas para Asserção-Razão). Nulo para outros tipos.
        5.  `alternativas`: Uma LISTA de dicionários, um para cada alternativa (A, B, C, D, E ou afirmativas I, II, III...). Cada dicionário deve ter:
            * `texto`: O texto completo da alternativa/afirmativa.
            * `eh_correta`: Um valor booleano (true/false) indicando se esta é a alternativa/afirmativa correta. Tente inferir a correta a partir da justificativa ou de marcações no texto original, se houver. Se o gabarito não estiver explícito no original, retorne erro; não invente nem escolha uma alternativa por padrão.
        6.  `justificativa`: O texto completo que explica as respostas. Pode ser nulo.

        Retorne sua resposta como uma LISTA DE OBJETOS JSON, onde cada objeto representa UMA questão extraída. NÃO inclua NENHUM texto, explicação ou formatação fora da lista JSON. A resposta deve começar com '[' e terminar com ']'.

        Exemplo de formato de retorno esperado para DUAS questões:
        [
          {{
            "tipo_questao": "RESPOSTA_UNICA",
            "texto_base": "Paciente X...",
            "enunciado": "Qual o diagnóstico?",
            "proposicao_dois": null,
            "alternativas": [
              {{"texto": "Alternativa A", "eh_correta": false}},
              {{"texto": "Alternativa B - Correta", "eh_correta": true}},
              {{"texto": "Alternativa C", "eh_correta": false}},
              {{"texto": "Alternativa D", "eh_correta": false}},
              {{"texto": "Alternativa E", "eh_correta": false}}
            ],
            "justificativa": "Explicação detalhada..."
          }},
          {{
            "tipo_questao": "MULTIPLA_ESCOLHA",
            "texto_base": "Considerando Y...",
            "enunciado": "Avalie as afirmativas:",
            "proposicao_dois": null,
            "alternativas": [
              {{"texto": "Afirmativa I...", "eh_correta": true}},
              {{"texto": "Afirmativa II...", "eh_correta": false}},
              {{"texto": "Afirmativa III...", "eh_correta": true}}
            ],
            "justificativa": "Justificativa das afirmativas..."
          }}
        ]

        TEXTO FORNECIDO PARA ANÁLISE:
        --- início do texto ---
        {texto_bruto}
        --- fim do texto ---

        Agora, gere a lista JSON com as questões extraídas:
        """

        resposta_texto = generate_text(prompt, request_id=request_id, json_output=True)

        # --- Tentativa de Limpeza da Resposta da IA ---
        # A IA pode retornar o JSON dentro de blocos de código markdown (```json ... ```)
        # ou com texto antes/depois. Tentamos extrair apenas o JSON válido.

        # Procura pelo início '[' e fim ']' do JSON
        match = re.search(r"\[.*\]", resposta_texto, re.DOTALL)
        if match:
            json_str = match.group(0)
            try:
                dados_estruturados = json.loads(json_str)
                # Verifica se o resultado é realmente uma lista
                if isinstance(dados_estruturados, list):
                    return dados_estruturados
                else:
                    print("Erro de Parsing: IA retornou JSON, mas não era uma lista.")
                    return {"erro": "A IA retornou um JSON, mas o formato não é uma lista como esperado."}
            except json.JSONDecodeError:
                return {"erro": "A IA retornou JSON inválido. O lote não foi importado."}
        return {"erro": "A IA não retornou uma lista válida. O lote não foi importado."}
    except AIControlError as error:
        return {"erro": str(error)}
    except Exception:
        return {"erro": "Falha na comunicação com a IA. O lote não foi importado."}


def validate_import(data, componente, backup=False):
    if not isinstance(data, list) or not 1 <= len(data) <= 100:
        raise ValidationError("Envie um lote de 1 a 100 questões válidas.")
    for item in data:
        if not isinstance(item, dict):
            raise ValidationError("Item inválido no lote.")
        style = item.get("tipo_questao", "RESPOSTA_UNICA")
        if style not in allowed_styles(componente):
            raise ValidationError("Estilo inválido para este componente.")
        if item.get("uso_prova", "INTEGRADA") not in dict(Questao.USO_PROVA_CHOICES):
            raise ValidationError("Destino inválido no lote.")
        for field in ("texto_base", "enunciado", "justificativa", "proposicao_dois", "comentario_validacao"):
            value = item.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 100000):
                raise ValidationError("Texto inválido ou muito longo no lote.")
        if not item.get("enunciado") or not item.get("justificativa"):
            raise ValidationError("O lote contém uma questão sem enunciado ou justificativa.")
        alternatives = item.get("alternativas")
        if not isinstance(alternatives, list) or not 2 <= len(alternatives) <= 5:
            raise ValidationError("Alternativas inválidas no lote.")
        for alt in alternatives:
            if (
                not isinstance(alt, dict)
                or not isinstance(alt.get("texto"), str)
                or not 1 <= len(alt["texto"].strip()) <= 500
            ):
                raise ValidationError("Texto de alternativa inválido no lote.")
            correct = alt.get("correta", alt.get("eh_correta")) if backup else alt.get("eh_correta")
            if type(correct) is not bool:
                raise ValidationError("Gabarito inválido no lote.")
            alt["eh_correta"] = correct
        if len({a["texto"].strip().casefold() for a in alternatives}) != len(alternatives):
            raise ValidationError("Alternativas repetidas no lote.")
        correct = sum(a["eh_correta"] for a in alternatives)
        if (style == "MULTIPLA_ESCOLHA" and not 0 < correct < len(alternatives)) or (
            style != "MULTIPLA_ESCOLHA" and correct != 1
        ):
            raise ValidationError("Gabarito inconsistente no lote.")
        if style == "ASSERCAO_RAZAO" and not item.get("proposicao_dois"):
            raise ValidationError("Segunda proposição ausente no lote.")
        if not backup:
            if style == "ASSERCAO_RAZAO":
                item["assercao_gabarito"] = "ABCDE"[
                    next(i for i, a in enumerate(alternatives) if a["eh_correta"])
                ]
            validate_ai_draft(item, style, alternative_count(componente))
    return data
