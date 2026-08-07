# Em: bulk_submit/utils.py

import google.generativeai as genai
from django.conf import settings
import json
import re # Para ajudar a limpar a resposta da IA

def processar_texto_com_ia(texto_bruto):
    """
    Envia o texto bruto contendo múltiplas questões para a API do Gemini
    e tenta retornar uma lista de dicionários, cada um representando uma questão.
    """
    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash') # Usando um modelo estável

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
            * `eh_correta`: Um valor booleano (true/false) indicando se esta é a alternativa/afirmativa correta. Tente inferir a correta a partir da justificativa ou de marcações no texto original, se houver. Se não for óbvio, marque a primeira como 'true' por padrão para Resposta Única.
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

        response = model.generate_content(prompt)

        # --- Tentativa de Limpeza da Resposta da IA ---
        # A IA pode retornar o JSON dentro de blocos de código markdown (```json ... ```)
        # ou com texto antes/depois. Tentamos extrair apenas o JSON válido.
        resposta_texto = response.text

        # Procura pelo início '[' e fim ']' do JSON
        match = re.search(r'\[.*\]', resposta_texto, re.DOTALL)
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
            except json.JSONDecodeError as json_err:
                print(f"Erro de Parsing: Falha ao decodificar JSON da IA. Erro: {json_err}")
                print(f"String JSON que falhou: {json_str[:500]}...") # Mostra o início do JSON problemático
                return {"erro": f"A IA retornou um texto que parece JSON, mas falhou na decodificação: {json_err}"}
        else:
            # Se não encontrou nem '[' e ']', a resposta está muito fora do esperado
            print(f"Erro de Parsing: Resposta da IA não contém uma lista JSON válida. Resposta: {resposta_texto[:500]}...")
            return {"erro": "A resposta da IA não continha uma lista JSON válida como solicitado."}

    except Exception as e:
        print(f"Erro geral na chamada da IA: {e}")
        return {"erro": f"Falha na comunicação com a API do Gemini: {str(e)}"}