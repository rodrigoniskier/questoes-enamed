# Em: questoes/utils.py

import google.generativeai as genai
from django.conf import settings
from django.core.mail import send_mail
from .models import ComponenteCurricular
import json
import re

# --- FUNÇÃO DE E-MAIL (MANTIDA) ---
def enviar_email_status(questao):
    """
    Envia um e-mail para o professor informando a mudança de status da questão.
    """
    if not questao.professor_email:
        print(f"Aviso: Tentativa de notificar sobre a questão ID {questao.id}, mas não há e-mail de professor cadastrado.")
        return

    if questao.status == 'APROVADA':
        assunto = f"Sua questão foi Aprovada!"
        mensagem = (
            f"Olá, {questao.professor_nome},\n\n"
            f"Temos uma ótima notícia! Sua questão sobre '{questao.componente.nome}' foi APROVADA.\n\n"
            f"Enunciado: \"{questao.enunciado[:80]}...\"\n\n"
            f"Agradecemos sua valiosa contribuição para o nosso banco de questões.\n\n"
            f"Atenciosamente,\n"
            f"NAPED - Medicina UNIPÊ"
        )
    elif questao.status == 'REPROVADA':
        assunto = f"Feedback sobre sua questão submetida"
        mensagem = (
            f"Olá, {questao.professor_nome},\n\n"
            f"Sua questão sobre '{questao.componente.nome}' foi avaliada e precisa de ajustes.\n\n"
            f"Status: REPROVADA\n"
            f"Enunciado: \"{questao.enunciado[:80]}...\"\n\n"
            f"Relatório de Análise:\n"
            f"--------------------------------\n"
            f"{questao.comentario_validacao or 'Nenhum comentário adicional fornecido.'}\n"
            f"--------------------------------\n\n"
            f"Por favor, revise a questão com base no feedback e, se desejar, submeta uma nova versão.\n\n"
            f"Atenciosamente,\n"
            f"NAPED - Medicina UNIPÊ"
        )
    else:
        return

    try:
        send_mail(
            assunto,
            mensagem,
            settings.DEFAULT_FROM_EMAIL,
            [questao.professor_email],
            fail_silently=False,
        )
        print(f"E-mail de status '{questao.status}' enviado para {questao.professor_email} sobre a questão ID {questao.id}.")
    except Exception as e:
        print(f"ERRO ao enviar e-mail para {questao.professor_email} sobre a questão ID {questao.id}: {e}")
        raise e


# --- FUNÇÃO DE AVALIAÇÃO COM IA (MANTIDA) ---
def avaliar_questao_com_ia(questao):
    """
    Formata o prompt definido no código com os dados da questão,
    envia para a API do Gemini e retorna o relatório de texto gerado pela IA.
    """
    try:
        prompt_template = """
Você atuará como um Professor Doutor especialista em Avaliação Educacional e Psicometria...
... (todo o seu prompt de AVALIAÇÃO que já funciona) ...
IMPORTANTE: Sua resposta DEVE começar EXATAMENTE com "RELATÓRIO DE ANÁLISE DO ITEM"...
"""
        dados_formatados = {
            "tipo_questao": questao.get_tipo_questao_display(),
            "texto_base": questao.texto_base or "",
            "enunciado": questao.enunciado or "",
            "proposicao_dois": questao.proposicao_dois or 'N/A',
            "alternativas": [alt.texto for alt in questao.alternativas.all()],
            "justificativa": questao.justificativa or ""
        }

        prompt_final = prompt_template.format(**dados_formatados)

        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-3-flash-preview') # Modelo padronizado

        response = model.generate_content(prompt_final)

        relatorio_bruto = response.text.strip()
        relatorio_limpo = relatorio_bruto.replace('**', '')

        if "RELATÓRIO DE ANÁLISE DO ITEM" not in relatorio_limpo:
             return {"erro": f"Resposta inesperada da IA (não encontrou o início do relatório): {relatorio_limpo[:200]}..."}

        try:
            inicio_relatorio = relatorio_limpo.index("RELATÓRIO DE ANÁLISE DO ITEM")
            relatorio_final = relatorio_limpo[inicio_relatorio:]
        except ValueError:
             return {"erro": f"Erro ao extrair o relatório da resposta da IA: {relatorio_limpo[:200]}..."}

        return {"relatorio": relatorio_final}

    except Exception as e:
        return {"erro": f"Falha na avaliação com a IA: {str(e)}"}


# --- NOVA FUNÇÃO PARA GERAR QUESTÕES COM IA (PROMPT PARAMETRIZADO) ---
# AJUSTE: Adicionámos o argumento 'parametros' que vem do views.py
def gerar_questao_com_ia(prompt_professor: str, componente: ComponenteCurricular, tipo_questao: str, num_alternativas: int, parametros: dict = None) -> dict:
    """
    Gera o rascunho de uma questão com base no prompt do professor e nos
    parâmetros pedagógicos do ENAMED selecionados na interface.
    """
    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-3-flash-preview')

        # Garante que temos um dicionário mesmo se não for enviado
        if parametros is None:
            parametros = {}

        # --- PREPARAÇÃO DOS DADOS DO JSON ---
        alternativas_exemplo = [
            {"texto": "...", "eh_correta": False},
            {"texto": "...", "eh_correta": True},
            {"texto": "...", "eh_correta": False},
            {"texto": "...", "eh_correta": False}
        ]
        if num_alternativas == 5:
            alternativas_exemplo.append({"texto": "...", "eh_correta": False})

        justificativa_exemplo = "A) ERRADA. ... B) CERTA. ... C) ERRADA. ... D) ERRADA. ..."
        if num_alternativas == 5:
            justificativa_exemplo += " E) ERRADA. ..."

        alternativas_json_string = json.dumps(alternativas_exemplo, indent=16)

        # --- LÓGICA DA ÁREA SECUNDÁRIA ---
        area_primaria = parametros.get('area_primaria', 'Clínica Médica')
        area_secundaria = parametros.get('area_secundaria', 'Nenhuma')

        instrucao_areas = f"- **Área Principal:** {area_primaria}"
        if area_secundaria and area_secundaria != "Nenhuma":
            instrucao_areas += f"\n- **Área Secundária:** {area_secundaria}\n  *ATENÇÃO:* O cenário clínico DEVE ser interdisciplinar, integrando conhecimentos obrigatórios de ambas as áreas ({area_primaria} e {area_secundaria})."

        # --- CONSTRUÇÃO DO PROMPT MESTRE ATUALIZADO ---
        prompt_mestre = f"""
        Você atuará como um Médico Sênior e Professor Doutor especialista em {componente.nome},
        responsável por criar um item de avaliação (questão) para alunos de medicina.

        Seu objetivo é criar uma questão rigorosa, clara e pedagogicamente sólida,
        baseada nas diretrizes do ENAMED abaixo.

        =========================================================
        PARÂMETROS PEDAGÓGICOS (DIRETRIZES ENAMED)
        =========================================================
        * Nível Cognitivo (Bloom): {parametros.get('bloom', 'Aplicar')}
        * Nível de Dificuldade: {parametros.get('dificuldade', 'Médio')}

        ÁREAS DE FORMAÇÃO:
        {instrucao_areas}

        PERFIL DO AVALIADO A TESTAR:
        - {parametros.get('perfil', 'Defensor da cidadania e da dignidade humana...')}

        COMPETÊNCIA EXIGIDA:
        - {parametros.get('competencia', 'Reconhecer, diagnosticar e tratar urgências e emergências...')}

        DOMÍNIOS DE CONTEÚDO (Integrar na questão):
        1. {parametros.get('dc1', '')}
        2. {parametros.get('dc2', '')}
        3. {parametros.get('dc3', '')}

        COMANDO DO PROFESSOR (TEMA/CONTEXTO ESPECÍFICO):
        "{prompt_professor}"
        =========================================================

        DIRETRIZES ESTRUTURAIS OBRIGATÓRIAS:
        1.  Tipo de Questão Solicitado: {tipo_questao}

        2.  Texto-Base e Enunciado (REGRA DE OURO):
            - Crie um Texto-Base (ex: caso clínico, cenário) que seja curto, claro e ESSENCIAL.
            - Crie um Enunciado (comando) claro e direto.
            - A questão DEVE ser impossível de responder lendo apenas o enunciado. A resposta correta DEVE depender estritamente da análise das informações fornecidas no texto-base.

        3.  Alternativas:
            - Crie exatas {num_alternativas} alternativas.
            - Deve haver APENAS UMA resposta inquestionavelmente correta.
            - Os distratores (respostas incorretas) devem ser plausíveis, baseados em erros médicos comuns.
            - Todas as alternativas devem ter paralelismo sintático (ex: todas começam com verbo).
            - EVITE termos absolutos ("sempre", "nunca", "apenas").

        4.  Justificativa:
            - Crie uma justificativa detalhada, explicando por que a correta é a melhor conduta/diagnóstico e por que CADA UMA das outras alternativas está incorreta com base na literatura médica.

        SAÍDA (FORMATO OBRIGATÓRIO):
        Gere APENAS um objeto JSON válido, sem nenhum texto, saudações, avisos ou a formatação "```json".
        A chave "eh_correta" DEVE ser um booleano (true/false) e apenas UMA deve ser true.
        Estrutura exigida:
        {{
            "texto_base": "...",
            "enunciado": "...",
            "proposicao_dois": null,
            "alternativas": {alternativas_json_string},
            "justificativa": "{justificativa_exemplo}"
        }}
        """

        response = model.generate_content(prompt_mestre)
        resposta_texto = response.text.strip()

        # Limpeza simples para extrair o JSON (proteção contra formatação indesejada da IA)
        match = re.search(r'\{.*\}', resposta_texto, re.DOTALL)
        if match:
            json_str = match.group(0)
            try:
                dados_questao = json.loads(json_str)
                return dados_questao # Sucesso!
            except json.JSONDecodeError as json_err:
                print(f"Erro de Parsing IA (Geração): Falha ao decodificar JSON. Erro: {json_err}")
                return {"erro": f"A IA retornou um JSON inválido."}
        else:
            print(f"Erro de Parsing IA (Geração): Resposta não continha JSON. Resposta: {resposta_texto[:500]}...")
            return {"erro": "A IA não retornou um JSON válido. Tente novamente."}

    except Exception as e:
        print(f"Erro geral na chamada da IA (Geração): {e}")
        return {"erro": f"Falha na comunicação com a API do Gemini: {str(e)}"}