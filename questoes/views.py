# questoes/views.py

import csv
import json
import logging
import math
import random
import re
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.db.models import OuterRef, Q, Subquery
from django.forms import formset_factory
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from .ai_control import AIControlError, subject_key
from .answer_layout import LayoutBalanceError, montar_layout_balanceado, resumo_gabarito
from .answer_sheet import codigo_prova, linhas_do_gabarito, paginas_do_cartao
from .forms import AlternativaForm, QuestaoForm
from .models import ComponenteCurricular, Periodo, Questao, Semestre
from .multiple_choice import gerar_alternativas_multipla_escolha
from .submission import (
    ASSERTION_ANSWERS,
    FORM_ROUTES,
    MULTIPLE_STATEMENT_PROMPT,
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


# --- VIEW DE GESTÃO DO PROFESSOR (VISUALIZAR/EDITAR USO) ---
@never_cache
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

    from pre_auditoria.models import DecisaoPreAuditoria

    feedback = DecisaoPreAuditoria.objects.filter(
        questao_id=OuterRef("pk"), acao__in=["REPROVADA", "APROVADA_SUGESTOES"]
    ).exclude(justificativa="")
    questoes = (
        Questao.objects.filter(componente_id__in=componente.ids_compartilhados())
        .annotate(ultimo_parecer=Subquery(feedback.values("justificativa")[:1]))
        .prefetch_related("alternativas")
        .order_by("-id")
    )
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
    num_alternativas = alternative_count(componente, tipo_questao)
    FormSet = formset_factory(
        AlternativaForm,
        extra=num_alternativas,
        max_num=num_alternativas,
        validate_max=True,
        absolute_max=num_alternativas,
    )
    initial = {**get_header(request, componente), "componente": componente, "tipo_questao": tipo_questao}
    if tipo_questao == "MULTIPLA_ESCOLHA":
        initial["enunciado"] = MULTIPLE_STATEMENT_PROMPT
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
            except (DatabaseError, OSError) as error:
                logger.error(
                    "Submission persistence failed for component %s: %s", componente.pk, type(error).__name__
                )
                questao_form.add_error(
                    None,
                    "Não foi possível salvar. Seus textos foram preservados; tente novamente. Se enviou uma imagem, selecione-a novamente.",
                )
            else:
                if question is None:
                    messages.info(
                        request, "Este envio já foi processado. A questão foi removida e não será recriada."
                    )
                    target = reverse("submeter_selecao")
                    return (
                        JsonResponse({"redirect": target})
                        if request.headers.get("Accept") == "application/json"
                        else redirect(target)
                    )
                messages.success(
                    request,
                    "Questão enviada com sucesso!"
                    if created
                    else "Esta questão já foi enviada; nenhuma cópia foi criada.",
                )
                if action == "finish":
                    target = reverse("submeter_selecao")
                    return (
                        JsonResponse({"redirect": target})
                        if request.headers.get("Accept") == "application/json"
                        else redirect(target)
                    )
                continuation = remember_header(request, question)
                route = FORM_ROUTES[tipo_questao] if action == "same_style" else "submeter_selecao"
                target = (
                    reverse(route) + "?" + urlencode({"componente": componente.pk, "continuar": continuation})
                )
                return (
                    JsonResponse({"redirect": target})
                    if request.headers.get("Accept") == "application/json"
                    else redirect(target)
                )
    else:
        questao_form = QuestaoForm(initial=initial)
        alternativa_formset = FormSet(prefix="alternativas")
    if request.method == "POST" and request.headers.get("Accept") == "application/json":
        errors = [str(e) for values in questao_form.errors.values() for e in values]
        errors += [str(e) for e in alternativa_formset.non_form_errors()]
        errors += [
            str(e)
            for form_errors in alternativa_formset.errors
            for values in form_errors.values()
            for e in values
        ]
        return JsonResponse(
            {
                "erro": " ".join(errors) or "Revise os campos e tente novamente.",
                "submission_token": submission_token,
            },
            status=422,
        )
    return render(
        request,
        "questoes/submeter_form.html",
        {
            "questao_form": questao_form,
            "alternativa_formset": alternativa_formset,
            "componente": componente,
            "tipo_questao": tipo_questao,
            "enunciado_padrao_multipla": MULTIPLE_STATEMENT_PROMPT,
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
        dados_questoes = [
            {
                "id": q.pk,
                "enunciado": q.enunciado,
                "status": q.status,
                "status_descricao": q.get_status_display(),
            }
            for q in questoes
        ]
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
            or (
                not 3 <= num_alternativas <= 5
                if tipo_questao == "MULTIPLA_ESCOLHA"
                else num_alternativas != alternative_count(componente, tipo_questao)
            )
        ):
            return JsonResponse({"erro": "Estilo ou número de alternativas inválido."}, status=400)
        if any(
            value is not None and (not isinstance(value, str) or len(value) > 500)
            for value in parametros_ia.values()
        ):
            return JsonResponse({"erro": "Parâmetros pedagógicos inválidos."}, status=400)

        try:
            read_submission_token(data.get("submission_token", ""), componente, tipo_questao)
            request_id = str(uuid.UUID(data.get("request_id", "")))
        except (ValidationError, ValueError, TypeError, AttributeError):
            return JsonResponse(
                {
                    "erro": "Reabra o formulário para renovar a autorização de geração.",
                    "codigo": "formulario_invalido",
                },
                status=400,
            )

        # --- CHAMADA ATUALIZADA DA FUNÇÃO UTILS ---
        dados_questao_ia = gerar_questao_com_ia(
            prompt_professor=prompt_professor,
            componente=componente,
            tipo_questao=tipo_questao,
            num_alternativas=num_alternativas,
            parametros=parametros_ia,
            request_id=request_id,
            subject=subject_key(request.META.get("REMOTE_ADDR", "unknown")),
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

    except AIControlError as error:
        response = JsonResponse({"erro": str(error), "codigo": error.code}, status=error.status)
        if error.retry_after:
            response["Retry-After"] = str(error.retry_after)
        return response
    except (json.JSONDecodeError, UnicodeDecodeError):
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
    except Exception as error:
        logger.error("AI generation failed: %s", type(error).__name__)
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

            questoes_do_periodo = Questao.objects.filter(
                componente__periodo=periodo_selecionado, status__in=Questao.STATUS_APROVADOS
            ).order_by("componente__nome", "id")

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
    modo_preflight = request.GET.get("preflight") == "1"
    token_prova = request.GET.get("prova_token", "")
    tipo_prova = request.GET.get("tipo_prova", "integrada")
    exportar_txt = request.GET.get("formato_txt") == "1"

    def falha(mensagem, status=400):
        if modo_preflight:
            return JsonResponse({"erro": mensagem}, status=status)
        messages.error(request, mensagem)
        return redirect("pagina_gerar_prova")

    if token_prova:
        if modo_preflight or len(token_prova) > 12000:
            return falha("Solicitação de prova inválida.")
        try:
            dados = signing.loads(token_prova, salt="questoes.prova.preflight.v1", max_age=3600)
        except signing.BadSignature:
            return falha("A confirmação da prova expirou ou foi alterada. Gere uma nova prova.")
        ids = dados.get("ids")
        if (
            dados.get("usuario") != request.user.pk
            or not isinstance(ids, list)
            or not 1 <= len(ids) <= 300
            or any(type(pk) is not int or pk <= 0 for pk in ids)
            or len(set(ids)) != len(ids)
            or dados.get("tipo_prova")
            not in ("integrada", "internato", "reposicao", "residencia", "simulado")
            or not isinstance(dados.get("codigo"), str)
            or type(dados.get("exportar_txt")) is not bool
        ):
            return falha("Confirmação inválida. Gere uma nova prova.")

        questoes_db = Questao.objects.filter(pk__in=ids, status__in=Questao.STATUS_APROVADOS)
        mapa = {questao.pk: questao for questao in questoes_db}
        if len(mapa) != len(ids):
            return falha("A seleção da prova foi modificada. Gere uma nova prova.")
        questoes_selecionadas = [mapa[pk] for pk in ids]
        componentes_ids = {questao.componente_id for questao in questoes_selecionadas}
        componentes_da_prova = (
            ComponenteCurricular.objects.filter(pk__in=componentes_ids)
            .select_related("periodo__semestre")
            .order_by("nome")
        )
        tipo_prova = dados["tipo_prova"]
        exportar_txt = dados["exportar_txt"]
        try:
            alternativas_por_questao = montar_layout_balanceado(
                questoes_selecionadas, gerar_alternativas_multipla_escolha
            )
            codigo_atual = codigo_prova(questoes_selecionadas, alternativas_por_questao)
        except (LayoutBalanceError, ValueError) as erro:
            return falha(str(erro))
        if codigo_atual != dados["codigo"]:
            return falha("O conteúdo ou a disposição da prova mudou. Gere uma nova versão.")
    else:
        componentes_ids = request.GET.getlist("componentes")
        if not componentes_ids:
            return falha("Selecione pelo menos um componente.")
        if len(componentes_ids) > 100 or not all(pk.isdigit() for pk in componentes_ids):
            return falha("Selecione componentes válidos para a prova.")

        selecionados = ComponenteCurricular.objects.filter(pk__in=componentes_ids)
        canonicos = {c.consolidado_em_id or c.pk for c in selecionados}
        if not canonicos:
            return falha("Selecione pelo menos um componente válido para a prova.")
        componentes_da_prova = (
            ComponenteCurricular.objects.filter(pk__in=canonicos)
            .select_related("periodo__semestre")
            .order_by("nome")
        )
        quantidades = {}
        for componente in componentes_da_prova:
            valor = request.GET.get(f"quantidade_{componente.pk}", str(componente.numero_questoes_prova))
            if (
                len(valor) > 10
                or not valor.isdigit()
                or not 1 <= int(valor) <= componente.numero_questoes_prova
            ):
                return falha(
                    f"Informe de 1 a {componente.numero_questoes_prova} questões para {componente.nome}."
                )
            quantidades[componente.pk] = int(valor)

        bancos = []
        for componente in componentes_da_prova:
            query_base = Q(componente=componente, status__in=Questao.STATUS_APROVADOS)
            if tipo_prova == "reposicao":
                query_tipo = Q(uso_prova="REPOSICAO") | Q(uso_prova="AMBAS")
            elif tipo_prova == "residencia":
                query_tipo = Q(uso_prova="RESIDENCIA")
            elif tipo_prova == "simulado":
                query_tipo = Q(uso_prova="SIMULADO")
            else:
                query_tipo = Q(uso_prova="INTEGRADA") | Q(uso_prova="AMBAS")
            aprovadas = list(Questao.objects.filter(query_base & query_tipo))
            bancos.append((aprovadas, quantidades[componente.pk]))

        # Procura uma seleção balanceada. Se nenhuma das tentativas atingir a
        # meta, escolhe a melhor encontrada, sem jamais romper a ordem textual.
        melhor = None
        ultimo_erro = None
        for _ in range(20):
            sorteadas_total = []
            for aprovadas, quantidade in bancos:
                sorteadas = (
                    random.sample(aprovadas, quantidade) if len(aprovadas) >= quantidade else aprovadas
                )
                sorteadas_total.extend(sorteadas)
            if not sorteadas_total:
                return falha("Não há questões aprovadas para compor esta prova.")
            try:
                layout = montar_layout_balanceado(sorteadas_total, gerar_alternativas_multipla_escolha)
                resumo = resumo_gabarito(sorteadas_total, layout)
            except LayoutBalanceError as erro:
                ultimo_erro = erro
                continue

            contagens = list(resumo["contagens_quatro"].values())
            total_quatro = resumo["quatro_alternativas"]
            score = (
                max(contagens) - min(contagens),
                sum((4 * quantidade - total_quatro) ** 2 for quantidade in contagens),
            )
            if melhor is None or score < melhor[0]:
                melhor = (score, sorteadas_total, layout)
            if resumo["equilibrado"] or all(len(aprovadas) <= quantidade for aprovadas, quantidade in bancos):
                break

        if melhor is None:
            return falha(str(ultimo_erro or "Não foi possível montar uma prova válida."))
        _, questoes_selecionadas, alternativas_por_questao = melhor

    # Prévia da própria prova, com seleção e ordenação imutáveis no link
    # assinado. A abertura da nova aba acontece SOMENTE após confirmação.
    if modo_preflight:
        resumo = resumo_gabarito(questoes_selecionadas, alternativas_por_questao)
        codigo = codigo_prova(questoes_selecionadas, alternativas_por_questao)
        token = signing.dumps(
            {
                "usuario": request.user.pk,
                "ids": [questao.pk for questao in questoes_selecionadas],
                "tipo_prova": tipo_prova,
                "exportar_txt": exportar_txt,
                "codigo": codigo,
            },
            salt="questoes.prova.preflight.v1",
        )
        url = reverse("prova_gerada") + "?" + urlencode({"prova_token": token})
        resposta = JsonResponse(
            {
                **resumo,
                "codigo_prova": codigo,
                "url_abertura": url,
                "exportar_txt": exportar_txt,
            }
        )
        resposta["Cache-Control"] = "no-store"
        return resposta

    # ==========================================
    # LÓGICA DE EXPORTAÇÃO PARA .TXT (CORRIGIDA)
    # ==========================================
    if exportar_txt:
        linhas_txt = []
        for questao in questoes_selecionadas:
            texto_base = questao.texto_base.strip() if questao.texto_base else ""
            enunciado = questao.enunciado_para_prova

            # 1. Remove TODAS as quebras de linha do texto-base e enunciado
            # Isso garante que a questão inteira fique na mesma linha
            texto_base = re.sub(r"[\r\n]+", " ", texto_base).strip()
            enunciado = re.sub(r"[\r\n]+", " ", enunciado).strip()

            # 2. Monta o cabeçalho na mesma linha, separado por espaço e terminando com TAB
            if texto_base:
                cabecalho = f"MC\tContexto: {texto_base}"
                if enunciado:
                    cabecalho += f" Comando: {enunciado}"
                cabecalho += "\t"
            else:
                cabecalho = f"MC\tComando: {enunciado}\t"

            # Usa exatamente o mesmo layout balanceado da prova impressa.
            alts = alternativas_por_questao[questao.pk]

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
        questao.alternativas_finais = alternativas_por_questao[questao.pk]

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
    if not total_questoes:
        messages.error(request, "Não há questões aprovadas para compor esta prova.")
        return redirect("pagina_gerar_prova")

    try:
        codigo_cartao = codigo_prova(questoes_selecionadas, alternativas_por_questao)
        folhas_cartao = paginas_do_cartao(questoes_selecionadas, alternativas_por_questao)
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("pagina_gerar_prova")

    context = {
        "codigo_prova": codigo_cartao,
        "paginas_cartao": folhas_cartao,
        "componentes": componentes_da_prova,
        "questoes_selecionadas": questoes_selecionadas,
        "questao_ids": questao_ids,
        "titulo_topo": titulo_topo,
        "semestres_prova": sorted(
            {
                componente.periodo.semestre.nome
                for componente in componentes_da_prova
                if componente.periodo.semestre_id
            }
        ),
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

    questoes = Questao.objects.filter(id__in=ids_list, status__in=Questao.STATUS_APROVADOS)
    questoes_mapeadas = {q.id: q for q in questoes}
    questoes_ordenadas = [questoes_mapeadas[pk] for pk in ids_list if pk in questoes_mapeadas]
    try:
        alternativas_por_questao = montar_layout_balanceado(
            questoes_ordenadas, gerar_alternativas_multipla_escolha
        )
    except LayoutBalanceError as erro:
        messages.error(request, str(erro))
        return redirect("pagina_gerar_prova")

    letras = "ABCDE"
    for questao in questoes_ordenadas:
        alternativas = alternativas_por_questao[questao.pk]
        questao.alternativas_finais_exibicao = alternativas
        questao.letra_gabarito = next(
            (
                letras[i]
                for i, alt in enumerate(alternativas)
                if (alt["eh_correta"] if isinstance(alt, dict) else alt.eh_correta)
            ),
            "?",
        )

    componentes_ids = {q.componente.id for q in questoes_ordenadas}
    componentes = ComponenteCurricular.objects.filter(id__in=componentes_ids)

    try:
        codigo_cartao = codigo_prova(questoes_ordenadas, alternativas_por_questao)
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("pagina_gerar_prova")

    context = {
        "componentes": componentes,
        "questoes_ordenadas": questoes_ordenadas,
        "codigo_prova": codigo_cartao,
    }
    return render(request, "questoes/prova_gabarito.html", context)


@staff_member_required
@never_cache
def prova_gabarito_csv_view(request):
    """Exporta gabarito autoritativo para conferência externa das folhas."""
    ids_str = request.GET.get("ids", "")
    if not ids_str or len(ids_str) > 4000:
        return HttpResponse("Informe os IDs da prova.", status=400)

    partes = ids_str.split(",")
    if (
        len(partes) > 300
        or not all(parte.isdecimal() and 0 < int(parte) for parte in partes)
        or len(set(partes)) != len(partes)
    ):
        return HttpResponse("Lista de questões inválida.", status=400)

    ids = [int(parte) for parte in partes]
    questoes = Questao.objects.filter(pk__in=ids, status__in=Questao.STATUS_APROVADOS)
    mapa = {questao.pk: questao for questao in questoes}
    if len(mapa) != len(ids):
        return HttpResponse("Questões não encontradas ou não aprovadas.", status=400)

    ordenadas = [mapa[pk] for pk in ids]
    try:
        alternativas = montar_layout_balanceado(ordenadas, gerar_alternativas_multipla_escolha)
        codigo = codigo_prova(ordenadas, alternativas)
        linhas = linhas_do_gabarito(ordenadas, alternativas)
    except (LayoutBalanceError, ValueError) as erro:
        return HttpResponse(str(erro), status=422)

    resposta = HttpResponse(content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = f'attachment; filename="gabarito_{codigo}.csv"'
    resposta.write("\ufeff")
    escritor = csv.writer(resposta)
    escritor.writerow(
        [
            "codigo_prova",
            "numero_questao",
            "questao_id",
            "alternativas_validas",
            "resposta_correta",
            "total_questoes",
        ]
    )
    for linha in linhas:
        escritor.writerow(
            [
                codigo,
                linha["questao"],
                linha["questao_id"],
                "ABCDE"[: linha["quantidade_alternativas"]],
                linha["correta"],
                len(linhas),
            ]
        )
    return resposta


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
    if not request.user.has_perm("questoes.change_questao"):
        return HttpResponseForbidden("Você não tem permissão para editar questões.")
    get_object_or_404(Questao, pk=pk)
    return redirect("admin:questoes_questao_change", pk)


@require_GET
@never_cache
def api_version_view(request):
    return JsonResponse({"version": settings.RELEASE_VERSION, "revision": settings.RELEASE_REVISION})
