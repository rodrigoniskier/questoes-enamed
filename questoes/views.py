# questoes/views.py

import json
import logging
import math
import random
import re
from itertools import combinations
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import DatabaseError
from django.db.models import Q
from django.forms import formset_factory
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from .forms import AlternativaForm, QuestaoForm
from .models import Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .submission import (
    ASSERTION_ANSWERS,
    FORM_ROUTES,
    allowed_styles,
    alternative_count,
    get_header,
    new_submission_token,
    read_submission_token,
    remember_header,
    save_submission,
    validate_ai_draft,
    validate_alternatives,
)
from .utils import gerar_questao_com_ia

logger = logging.getLogger(__name__)


# --- FUNÇÃO AUXILIAR (ATUALIZADA: BALANCEAMENTO E 4 ALTERNATIVAS) ---
def gerar_alternativas_multipla_escolha(questao):
    """
    Gera exatas 4 alternativas finais para uma questão de Resposta Múltipla.
    REGRAS APLICADAS:
    1. Exatamente 4 alternativas (1 correta e 3 falsas).
    2. Sem "Nenhuma das afirmativas está correta".
    3. Dimensionamento balanceado da frequência de cada afirmativa.
    4. Ordem crescente na sequência lógica (I, II e III).
    """
    afirmativas = list(questao.alternativas.all().order_by("id"))
    indices_romanos = {i + 1: r for i, r in enumerate(["I", "II", "III", "IV", "V", "VI", "VII"])}

    # 1. Identifica os índices da resposta correta na base de dados
    indices_corretos = tuple(sorted([i + 1 for i, af in enumerate(afirmativas) if af.eh_correta]))

    def formatar_texto(indices):
        # Como a opção vazia foi removida, garantimos que indices sempre tem elementos
        romanos = [indices_romanos[i] for i in sorted(indices)]  # Garante ordem crescente
        if len(romanos) == 1:
            return f"Apenas a afirmativa {romanos[0]} está correta."

        prefixo = "Apenas as afirmativas"
        parte_romanos = " e ".join([", ".join(romanos[:-1]), romanos[-1]])
        return f"{prefixo} {parte_romanos} estão corretas."

    if not indices_corretos or len(afirmativas) < 3 or len(afirmativas) > 5:
        return [{"texto": "Questão com afirmativas inválidas: revise no banco.", "eh_correta": False}]
    texto_correto = formatar_texto(indices_corretos)

    # 2. Gera TODAS as combinações possíveis (excluindo a vazia/nenhuma)
    todos_indices = list(range(1, len(afirmativas) + 1))
    todas_combinacoes = []

    # Começa em 1 para excluir combinações com 0 elementos ("Nenhuma")
    for i in range(1, len(todos_indices) + 1):
        todas_combinacoes.extend(list(combinations(todos_indices, i)))

    # Retira o gabarito real da lista de possíveis distratores (respostas erradas)
    if indices_corretos in todas_combinacoes:
        todas_combinacoes.remove(indices_corretos)

    # 3. Lógica de Dimensionamento (Balanceamento de Frequência)
    melhores_conjuntos = []
    menor_desequilibrio = float("inf")

    # Testa todos os agrupamentos de 3 distratores possíveis
    for conjunto_distratores in combinations(todas_combinacoes, 3):
        # Conta a frequência de cada algarismo romano neste cenário da prova (1 certa + 3 erradas)
        frequencias = {i: 0 for i in todos_indices}

        # Pontua os que aparecem na resposta correta
        for idx in indices_corretos:
            frequencias[idx] += 1

        # Pontua os que aparecem nos distratores sorteados
        for distrator in conjunto_distratores:
            for idx in distrator:
                frequencias[idx] += 1

        # Mede o nível de desequilíbrio matemático da questão
        max_freq = max(frequencias.values())
        min_freq = min(frequencias.values())
        desequilibrio = max_freq - min_freq

        # Guarda apenas os conjuntos mais perfeitamente balanceados
        if desequilibrio < menor_desequilibrio:
            menor_desequilibrio = desequilibrio
            melhores_conjuntos = [conjunto_distratores]
        elif desequilibrio == menor_desequilibrio:
            melhores_conjuntos.append(conjunto_distratores)

    # 4. Seleção Determinística da Prova (Garante que não muda no reload)
    rng = random.Random(questao.id)
    melhores_conjuntos.sort()  # Ordena matematicamente antes de escolher

    if melhores_conjuntos:
        conjunto_escolhido = rng.choice(melhores_conjuntos)
    else:
        # Fallback de segurança, caso a questão tenha combinações limitadas
        conjunto_escolhido = rng.sample(todas_combinacoes, min(3, len(todas_combinacoes)))

    # 5. Montagem Final (Junta a correta com as 3 falsas escolhidas)
    alternativas_finais = [{"texto": texto_correto, "eh_correta": True}]

    for combo in conjunto_escolhido:
        alternativas_finais.append({"texto": formatar_texto(combo), "eh_correta": False})

    # Embaralha a posição final (A, B, C, D) usando a semente fixa
    rng.shuffle(alternativas_finais)

    return alternativas_finais


# --- VIEW DE GESTÃO DO PROFESSOR (VISUALIZAR/EDITAR USO) ---
def visualizar_questoes_view(request):
    componente_id = request.GET.get("componente")
    if not componente_id or not componente_id.isdigit():
        return redirect("submeter_selecao")

    componente = get_object_or_404(ComponenteCurricular, id=componente_id).canonico

    if request.method == "POST":
        if not request.user.is_staff:
            return HttpResponseForbidden("A alteração de destinos exige acesso de administrador.")
        for key, value in request.POST.items():
            if key.startswith("uso_prova_"):
                questao_id = key.split("_")[2]
                try:
                    questao = Questao.objects.get(id=questao_id, componente=componente)
                    if value in dict(Questao.USO_PROVA_CHOICES) and questao.uso_prova != value:
                        questao.uso_prova = value
                        questao.save()
                except (Questao.DoesNotExist, ValueError):
                    continue
        return redirect(f"{request.path}?componente={componente_id}")

    questoes = Questao.objects.filter(componente=componente).prefetch_related("alternativas").order_by("-id")
    opcoes_uso = Questao.USO_PROVA_CHOICES

    context = {
        "componente": componente,
        "questoes": questoes,
        "opcoes_uso": opcoes_uso,
    }
    return render(request, "questoes/visualizar_questoes.html", context)


# --- VIEWS DE SUBMISSÃO ---
def submeter_selecao_view(request):
    context = {
        "periodos": Periodo.objects.filter(ativo=True, semestre__ativo=True).order_by("nome"),
        "semestres_ativos": Semestre.objects.filter(ativo=True),
    }
    componente_id = request.GET.get("componente")
    if componente_id and componente_id.isdigit() and request.GET.get("continuar"):
        componente = get_object_or_404(
            ComponenteCurricular, pk=componente_id, periodo__semestre__ativo=True
        ).canonico
        if get_header(request, componente):
            query = urlencode({"componente": componente.pk, "continuar": request.GET["continuar"]})
            context.update(
                componente_continuacao=componente,
                estilos=[
                    {"nome": label, "url": reverse(FORM_ROUTES[style]) + "?" + query}
                    for style, label in Questao.TIPO_QUESTAO_CHOICES
                    if style in allowed_styles(componente)
                ],
            )
    return render(request, "questoes/submeter_selecao.html", context)


def submeter_form_view(request, tipo_questao):
    componente_id = request.GET.get("componente")
    if not componente_id or not componente_id.isdigit():
        return redirect("submeter_selecao")
    componente = get_object_or_404(
        ComponenteCurricular.objects.select_related("periodo__semestre", "consolidado_em__periodo__semestre"),
        pk=componente_id,
    ).canonico
    if not componente.periodo.ativo or (
        componente.periodo.semestre and not componente.periodo.semestre.ativo
    ):
        messages.info(request, "Este semestre está arquivado. Selecione um componente do semestre atual.")
        return redirect("submeter_selecao")
    if tipo_questao not in allowed_styles(componente):
        messages.error(request, "Este componente permite apenas questões de resposta única.")
        return redirect("submeter_selecao")
    num_alternativas = alternative_count(componente)
    FormSet = formset_factory(
        AlternativaForm,
        extra=num_alternativas,
        max_num=num_alternativas,
        validate_max=True,
        absolute_max=num_alternativas,
    )
    initial = {**get_header(request, componente), "componente": componente, "tipo_questao": tipo_questao}
    submission_token = new_submission_token(componente, tipo_questao)
    action = request.POST.get("submit_action", "finish")
    if request.method == "POST":
        # Route and component define the question; hidden input tampering is rejected.
        questao_form = QuestaoForm(request.POST, request.FILES)
        alternativa_formset = FormSet(request.POST, prefix="alternativas")
        valid = questao_form.is_valid()
        if (
            request.POST.get("componente") not in {str(pk) for pk in componente.ids_compartilhados()}
            or request.POST.get("tipo_questao") != tipo_questao
        ):
            questao_form.add_error(None, "O componente e o estilo devem corresponder ao formulário aberto.")
            valid = False
        if action not in ("finish", "same_style", "other_style"):
            questao_form.add_error(None, "Escolha uma ação de envio válida.")
            valid = False
        alternatives = []
        try:
            token_id = read_submission_token(
                request.POST.get("submission_token", ""), componente, tipo_questao
            )
            submission_token = request.POST["submission_token"]
        except ValidationError as error:
            questao_form.add_error(None, error)
            valid = False
        if tipo_questao == "ASSERCAO_RAZAO":
            answer = request.POST.get("assercao_gabarito")
            if answer not in ASSERTION_ANSWERS:
                questao_form.add_error(None, "Selecione o gabarito da relação entre as proposições.")
                valid = False
            alternatives = [
                {"texto": f"{letter}) {text}", "eh_correta": letter == answer}
                for letter, text in ASSERTION_ANSWERS.items()
            ]
        else:
            if not alternativa_formset.is_valid():
                valid = False
            else:
                alternatives = [f.cleaned_data for f in alternativa_formset if f.cleaned_data.get("texto")]
                try:
                    validate_alternatives(alternatives, tipo_questao, num_alternativas)
                except ValidationError as error:
                    questao_form.add_error(None, error)
                    valid = False
        if valid:
            try:
                question, created = save_submission(questao_form, alternatives, token_id)
            except (DatabaseError, OSError):
                logger.exception("Submission persistence failed for component %s", componente.pk)
                questao_form.add_error(
                    None,
                    "Não foi possível salvar. Seus textos foram preservados; tente novamente. Se enviou uma imagem, selecione-a novamente.",
                )
            else:
                if question is None:
                    messages.info(
                        request, "Este envio já foi processado. A questão foi removida e não será recriada."
                    )
                    return redirect("submeter_selecao")
                messages.success(
                    request,
                    "Questão enviada com sucesso!"
                    if created
                    else "Esta questão já foi enviada; nenhuma cópia foi criada.",
                )
                if action == "finish":
                    return redirect("submeter_selecao")
                continuation = remember_header(request, question)
                route = FORM_ROUTES[tipo_questao] if action == "same_style" else "submeter_selecao"
                return redirect(
                    reverse(route) + "?" + urlencode({"componente": componente.pk, "continuar": continuation})
                )
    else:
        questao_form = QuestaoForm(initial=initial)
        alternativa_formset = FormSet(prefix="alternativas")
    return render(
        request,
        "questoes/submeter_form.html",
        {
            "questao_form": questao_form,
            "alternativa_formset": alternativa_formset,
            "componente": componente,
            "tipo_questao": tipo_questao,
            "num_alternativas": num_alternativas,
            "submission_token": submission_token,
            "assercao_gabarito": request.POST.get("assercao_gabarito", ""),
        },
    )


# --- APIS ---
def get_componentes_por_periodo(request):
    periodo_ids_str = request.GET.get("periodo_ids")
    periodo_id_str = request.GET.get("periodo_id")
    ids_para_filtrar = []
    if periodo_ids_str:
        ids_para_filtrar = periodo_ids_str.split(",")
    elif periodo_id_str:
        ids_para_filtrar = [periodo_id_str]
    if not ids_para_filtrar:
        return JsonResponse([], safe=False)
    if len(ids_para_filtrar) > 100 or not all(value.isdigit() for value in ids_para_filtrar):
        return JsonResponse({"error": "Período inválido"}, status=400)
    componentes = ComponenteCurricular.objects.filter(
        periodo_id__in=ids_para_filtrar,
        periodo__ativo=True,
        periodo__semestre__ativo=True,
        consolidado_em__isnull=True,
    ).order_by("nome")
    return JsonResponse(list(componentes.values("id", "nome", "numero_questoes_prova")), safe=False)


def get_historico_questoes(request):
    componente_id = request.GET.get("componente_id")
    if not componente_id or not componente_id.isdigit():
        return JsonResponse({"error": "Componente inválido"}, status=400)
    try:
        componente = ComponenteCurricular.objects.get(id=componente_id).canonico
        questoes = Questao.objects.filter(componente=componente).order_by("-id")
        total_necessario = componente.numero_questoes_prova
        total_enviado = questoes.count()
        restantes = total_necessario - total_enviado
        dados_questoes = list(questoes.values("id", "enunciado", "status"))
        resposta = {
            "questoes": dados_questoes,
            "contagem": {
                "necessario": total_necessario,
                "enviado": total_enviado,
                "restantes": restantes if restantes > 0 else 0,
            },
        }
        return JsonResponse(resposta)
    except ComponenteCurricular.DoesNotExist:
        return JsonResponse({"error": "Componente não encontrado"}, status=404)


@require_GET
@never_cache
@ensure_csrf_cookie
def api_csrf_view(request):
    """Provide a fresh same-origin CSRF token without altering form data."""
    return JsonResponse({"csrfToken": get_token(request)})


@never_cache
def api_gerar_questao_view(request):
    if request.method != "POST":
        response = JsonResponse(
            {"erro": "Esta operação exige POST.", "codigo": "metodo_invalido"}, status=405
        )
        response["Allow"] = "POST"
        return response
    if request.content_type != "application/json":
        return JsonResponse({"erro": "Envie os dados em JSON.", "codigo": "tipo_invalido"}, status=415)
    if len(request.body) > 65536:
        return JsonResponse(
            {"erro": "A solicitação ultrapassa o tamanho permitido.", "codigo": "muito_grande"},
            status=413,
        )
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return JsonResponse({"erro": "Envie um objeto JSON."}, status=400)
        prompt_professor = data.get("prompt_professor")
        componente_id = data.get("componente_id")
        tipo_questao = data.get("tipo_questao")
        num_alternativas = data.get("num_alternativas")

        # --- NOVOS DADOS EXTRAÍDOS DO FRONTEND ---
        parametros_ia = {
            "area_primaria": data.get("area_primaria"),
            "area_secundaria": data.get("area_secundaria"),
            "bloom": data.get("bloom"),
            "dificuldade": data.get("dificuldade"),
            "perfil": data.get("perfil"),
            "competencia": data.get("competencia"),
            "dc1": data.get("dc1"),
            "dc2": data.get("dc2"),
            "dc3": data.get("dc3"),
        }

        if not all([prompt_professor, componente_id, tipo_questao, num_alternativas]):
            return JsonResponse({"erro": "Dados principais incompletos."}, status=400)

        if not isinstance(prompt_professor, str) or not 1 <= len(prompt_professor.strip()) <= 5000:
            return JsonResponse({"erro": "Informe um tema de até 5000 caracteres."}, status=400)
        if not str(componente_id).isdigit():
            return JsonResponse({"erro": "Componente inválido."}, status=400)
        componente = (
            ComponenteCurricular.objects.select_related(
                "periodo__semestre", "consolidado_em__periodo__semestre"
            )
            .get(id=componente_id)
            .canonico
        )
        if not componente.periodo.ativo or (
            componente.periodo.semestre and not componente.periodo.semestre.ativo
        ):
            return JsonResponse({"erro": "O semestre deste componente está arquivado."}, status=400)
        if (
            tipo_questao not in allowed_styles(componente)
            or type(num_alternativas) is not int
            or num_alternativas != alternative_count(componente)
        ):
            return JsonResponse({"erro": "Estilo ou número de alternativas inválido."}, status=400)
        if any(
            value is not None and (not isinstance(value, str) or len(value) > 500)
            for value in parametros_ia.values()
        ):
            return JsonResponse({"erro": "Parâmetros pedagógicos inválidos."}, status=400)

        # --- CHAMADA ATUALIZADA DA FUNÇÃO UTILS ---
        dados_questao_ia = gerar_questao_com_ia(
            prompt_professor=prompt_professor,
            componente=componente,
            tipo_questao=tipo_questao,
            num_alternativas=num_alternativas,
            parametros=parametros_ia,  # Passamos o dicionário com os novos filtros
        )

        if isinstance(dados_questao_ia, dict) and "erro" in dados_questao_ia:
            return JsonResponse(
                {
                    "erro": "A geração por IA está temporariamente indisponível. Tente novamente.",
                    "codigo": "provedor_indisponivel",
                },
                status=502,
            )
        return JsonResponse(validate_ai_draft(dados_questao_ia, tipo_questao, num_alternativas))

    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido.", "codigo": "json_invalido"}, status=400)
    except ComponenteCurricular.DoesNotExist:
        return JsonResponse(
            {"erro": "Componente não encontrado.", "codigo": "componente_inexistente"},
            status=404,
        )
    except ValidationError:
        logger.warning("Invalid AI draft for component %s", componente_id)
        return JsonResponse(
            {
                "erro": "A IA retornou um rascunho inválido. Tente novamente.",
                "codigo": "rascunho_invalido",
            },
            status=502,
        )
    except Exception:
        logger.exception("AI generation failed")
        return JsonResponse(
            {
                "erro": "Não foi possível gerar o rascunho agora. Tente novamente.",
                "codigo": "erro_interno",
            },
            status=503,
        )


# --- MONTADOR MANUAL ---
@staff_member_required
def montador_manual_view(request):
    context = {}
    periodo_id = request.GET.get("periodo_id")

    if not periodo_id:
        # AJUSTE AQUI: Mostrar apenas os períodos ativos na primeira tela do montador
        context["periodos"] = Periodo.objects.filter(ativo=True, semestre__ativo=True).order_by("nome")
    else:
        try:
            periodo_selecionado = Periodo.objects.get(id=periodo_id)
            context["periodo_selecionado"] = periodo_selecionado

            questoes_do_periodo = Questao.objects.filter(componente__periodo=periodo_selecionado).order_by(
                "componente__nome", "id"
            )

            questoes_processadas = []
            letra_map = {0: "A", 1: "B", 2: "C", 3: "D", 4: "E"}

            for questao in questoes_do_periodo:
                if questao.tipo_questao == "ASSERCAO_RAZAO":
                    continue

                # Lógica Determinística para o Montador
                if questao.tipo_questao == "MULTIPLA_ESCOLHA":
                    alts = gerar_alternativas_multipla_escolha(questao)
                    letra_correta = "?"
                    for i, alt in enumerate(alts):
                        if alt["eh_correta"]:
                            letra_correta = letra_map.get(i, "?")
                            break
                    questao.letra_correta_calculada = letra_correta
                else:
                    letra_correta = "?"
                    alternativas = list(questao.alternativas.all().order_by("id"))
                    for i, alt in enumerate(alternativas):
                        if alt.eh_correta:
                            letra_correta = letra_map.get(i, "E+")
                            break
                    questao.letra_correta_calculada = letra_correta

                questoes_processadas.append(questao)

            context["questoes"] = questoes_processadas

        except Periodo.DoesNotExist:
            # AJUSTE AQUI: Redundância caso o período não exista, carrega apenas os ativos
            context["periodos"] = Periodo.objects.filter(ativo=True, semestre__ativo=True).order_by("nome")

    return render(request, "questoes/montador_manual.html", context)


# --- GERADOR DE PROVAS E GABARITOS ---
@staff_member_required
def pagina_gerar_prova(request):
    # AJUSTE AQUI: Página de gerar prova foca apenas no que está ativo agora
    periodos = Periodo.objects.filter(ativo=True, semestre__ativo=True).order_by("nome")
    context = {"periodos": periodos}
    return render(request, "questoes/gerar_prova.html", context)


# Em questoes/views.py


@staff_member_required
def prova_gerada_view(request):
    componentes_ids = request.GET.getlist("componentes")
    tipo_prova = request.GET.get("tipo_prova", "integrada")

    # Verifica se o utilizador pediu para exportar em TXT
    exportar_txt = request.GET.get("formato_txt") == "1"

    if not componentes_ids:
        return redirect("pagina_gerar_prova")

    if len(componentes_ids) > 100 or not all(pk.isdigit() for pk in componentes_ids):
        messages.error(request, "Selecione componentes válidos para a prova.")
        return redirect("pagina_gerar_prova")
    # Previous links may include more than one alias of the same shared bank.
    selecionados = ComponenteCurricular.objects.filter(pk__in=componentes_ids)
    canonicos = {c.consolidado_em_id or c.pk for c in selecionados}
    if not canonicos:
        messages.error(request, "Selecione pelo menos um componente válido para a prova.")
        return redirect("pagina_gerar_prova")
    componentes_da_prova = ComponenteCurricular.objects.filter(pk__in=canonicos).order_by("nome")
    quantidades = {}
    for componente in componentes_da_prova:
        valor = request.GET.get(f"quantidade_{componente.pk}", str(componente.numero_questoes_prova))
        if len(valor) > 10 or not valor.isdigit() or not 1 <= int(valor) <= componente.numero_questoes_prova:
            messages.error(
                request, f"Informe de 1 a {componente.numero_questoes_prova} questões para {componente.nome}."
            )
            return redirect("pagina_gerar_prova")
        quantidades[componente.pk] = int(valor)
    questoes_selecionadas = []

    for componente in componentes_da_prova:
        query_base = Q(componente=componente, status="APROVADA")

        if tipo_prova == "reposicao":
            query_tipo = Q(uso_prova="REPOSICAO") | Q(uso_prova="AMBAS")
        elif tipo_prova == "residencia":
            query_tipo = Q(uso_prova="RESIDENCIA")
        elif tipo_prova == "simulado":
            query_tipo = Q(uso_prova="SIMULADO")
        else:
            query_tipo = Q(uso_prova="INTEGRADA") | Q(uso_prova="AMBAS")

        questoes_aprovadas = list(Questao.objects.filter(query_base & query_tipo))
        num_questoes = quantidades[componente.pk]

        if len(questoes_aprovadas) >= num_questoes:
            questoes_sorteadas = random.sample(questoes_aprovadas, num_questoes)
        else:
            questoes_sorteadas = questoes_aprovadas

        questoes_selecionadas.extend(questoes_sorteadas)

    # ==========================================
    # LÓGICA DE EXPORTAÇÃO PARA .TXT (CORRIGIDA)
    # ==========================================
    if exportar_txt:
        linhas_txt = []
        for questao in questoes_selecionadas:
            texto_base = questao.texto_base.strip() if questao.texto_base else ""
            enunciado = questao.enunciado.strip() if questao.enunciado else ""

            # 1. Remove TODAS as quebras de linha do texto-base e enunciado
            # Isso garante que a questão inteira fique na mesma linha
            texto_base = re.sub(r"[\r\n]+", " ", texto_base).strip()
            enunciado = re.sub(r"[\r\n]+", " ", enunciado).strip()

            # 2. Monta o cabeçalho na mesma linha, separado por espaço e terminando com TAB
            if texto_base:
                cabecalho = f"MC\tContexto: {texto_base} Comando: {enunciado}\t"
            else:
                cabecalho = f"MC\tComando: {enunciado}\t"

            # Gera as alternativas
            if questao.tipo_questao == "MULTIPLA_ESCOLHA":
                alts = gerar_alternativas_multipla_escolha(questao)
            else:
                alts = list(questao.alternativas.all().order_by("id"))

            alts_str_parts = []
            for alt in alts:
                is_correta = alt["eh_correta"] if isinstance(alt, dict) else alt.eh_correta
                texto_alt = alt["texto"] if isinstance(alt, dict) else alt.texto

                status = "correct" if is_correta else "incorrect"

                # Limpa prefixos como "A) " do início
                texto_limpo = re.sub(r"^[A-Ea-e][\)\.\-]\s*", "", texto_alt).strip()

                # 3. Limpeza AGRESSIVA de quebras de linha e tabs nas alternativas
                texto_limpo = re.sub(r"[\r\n\t]+", " ", texto_limpo).strip()

                # Junta o texto da alternativa com o status usando TAB
                alts_str_parts.append(f"{texto_limpo}\t{status}")

            # 4. Junta todas as alternativas com TAB na mesma linha do cabeçalho
            linha_completa = cabecalho + "\t".join(alts_str_parts)
            linhas_txt.append(linha_completa)

        # Junta todas as questões prontas com 2 quebras de linha de distância entre uma e outra
        conteudo_final = "\n\n".join(linhas_txt)

        # Retorna o arquivo de texto para download
        response = HttpResponse(conteudo_final, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="banco_questoes_{tipo_prova}.txt"'
        return response

    # ==========================================
    # LÓGICA DE GERAÇÃO DE PDF (MANTIDA)
    # ==========================================
    if tipo_prova == "reposicao":
        titulo_topo = "PROVA DE REPOSIÇÃO"
    elif tipo_prova == "residencia":
        titulo_topo = "PROVA DE RESIDÊNCIA MÉDICA - UNIPÊ"
    elif tipo_prova == "simulado":
        titulo_topo = "SIMULADO"
    else:
        periodos_da_prova = set(c.periodo.nome for c in componentes_da_prova)
        if len(periodos_da_prova) == 1:
            titulo_topo = periodos_da_prova.pop()
        else:
            titulo_topo = "PROVA INTEGRADA MULTIPERÍODO"

    anexo_imagens = []
    imagem_counter = 1

    for i, questao in enumerate(questoes_selecionadas):
        if questao.tipo_questao == "MULTIPLA_ESCOLHA":
            questao.alternativas_finais = gerar_alternativas_multipla_escolha(questao)
        else:
            questao.alternativas_finais = questao.alternativas.all().order_by("id")

        if questao.imagem:
            questao.numero_imagem_anexo = imagem_counter
            anexo_imagens.append(
                {
                    "numero_imagem": imagem_counter,
                    "numero_questao": i + 1,
                    "url": questao.imagem.url,
                }
            )
            imagem_counter += 1

    questao_ids = [q.id for q in questoes_selecionadas]
    total_questoes = len(questoes_selecionadas)
    meio = math.ceil(total_questoes / 2)

    context = {
        "componentes": componentes_da_prova,
        "questoes_selecionadas": questoes_selecionadas,
        "questao_ids": questao_ids,
        "titulo_topo": titulo_topo,
        "tipo_prova": tipo_prova,
        "anexo_imagens": anexo_imagens,
        "meio": meio,
    }

    return render(request, "questoes/prova_template.html", context)


@staff_member_required
def prova_gabarito_view(request):
    ids_str = request.GET.get("ids")
    if not ids_str:
        return redirect("pagina_gerar_prova")

    ids_list = [int(id) for id in ids_str.split(",")]

    questoes = Questao.objects.filter(id__in=ids_list)
    questoes_mapeadas = {q.id: q for q in questoes}
    questoes_ordenadas = []

    for id in ids_list:
        if id in questoes_mapeadas:
            questao = questoes_mapeadas[id]
            questao.letra_gabarito = "?"

            if questao.tipo_questao == "MULTIPLA_ESCOLHA":
                # Usa a função determinística (mesma semente = mesma ordem da prova)
                alternativas_geradas = gerar_alternativas_multipla_escolha(questao)

                letras = ["A", "B", "C", "D", "E"]
                for i, alt in enumerate(alternativas_geradas):
                    if alt["eh_correta"]:
                        questao.letra_gabarito = letras[i] if i < 5 else "?"
                        break

                questao.alternativas_finais_exibicao = alternativas_geradas

            else:
                alternativas = list(questao.alternativas.all().order_by("id"))
                letras = ["A", "B", "C", "D", "E"]
                for i, alt in enumerate(alternativas):
                    if alt.eh_correta:
                        questao.letra_gabarito = letras[i] if i < 5 else "?"
                        break
                questao.alternativas_finais_exibicao = alternativas

            questoes_ordenadas.append(questao)

    componentes_ids = {q.componente.id for q in questoes_ordenadas}
    componentes = ComponenteCurricular.objects.filter(id__in=componentes_ids)

    context = {
        "componentes": componentes,
        "questoes_ordenadas": questoes_ordenadas,
    }
    return render(request, "questoes/prova_gabarito.html", context)


# --- RELATÓRIOS ---
def relatorio_geral_view(request):
    # O relatório geral mantém a exibição de todas as questões do banco para controle de auditoria
    questoes = (
        Questao.objects.all()
        .select_related("componente", "componente__periodo")
        .prefetch_related("alternativas")
        .order_by("componente__periodo__nome", "componente__nome", "id")
    )
    context = {"questoes": questoes, "total_questoes": questoes.count()}
    return render(request, "questoes/relatorio_geral.html", context)


# --- ADMIN ---
@staff_member_required
def admin_editar_questao(request, pk):
    questao = get_object_or_404(Questao, pk=pk)
    alternativas = Alternativa.objects.filter(questao=questao).order_by("id")
    mensagem = ""

    if request.method == "POST":
        questao.texto_base = request.POST.get("texto_base", "")
        questao.enunciado = request.POST.get("enunciado", "")
        questao.justificativa = request.POST.get("justificativa", "")
        questao.status = request.POST.get("status", "pendente")
        questao.comentario_validacao = request.POST.get("comentario_avaliador", "")
        if request.FILES.get("imagem"):
            questao.imagem = request.FILES["imagem"]
        questao.save()

        for i, alternativa in enumerate(alternativas, 1):
            novo_texto = request.POST.get(f"alternativa_{i}", "")
            alternativa.texto = novo_texto
            alternativa.save()

        send_mail(
            "Sua questão foi avaliada",
            f"Status: {questao.status.upper()}\n\nParecer do avaliador: {questao.comentario_validacao}",
            "no-reply@seuprojeto.com",
            [questao.professor_email],
            fail_silently=False,
        )
        mensagem = "Questão salva e e-mail enviado ao professor."

    return render(
        request, "admin/questoes/questao/edicao_unificada.html", {"questao": questao, "mensagem": mensagem}
    )
