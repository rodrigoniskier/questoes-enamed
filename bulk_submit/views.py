# bulk_submit/views.py
import csv
import json
import logging
import re  # <--- Nova importação para a Mágica
from io import StringIO  # <--- Trocamos o TextIOWrapper pelo StringIO

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import redirect, render

from questoes.models import Alternativa, ComponenteCurricular, Periodo, Questao

from .forms import BulkSubmitForm, UploadGradeCSVForm
from .utils import processar_texto_com_ia

logger = logging.getLogger(__name__)


# ==========================================
# VIEW 1: SUBMISSÃO DE QUESTÕES (IA / JSON)
# ==========================================
@staff_member_required
def bulk_submit_view(request):
    """
    View para exibir o formulário de submissão em massa e processar os dados.
    Aceita tanto a leitura de arquivos JSON de backup quanto o texto bruto via IA.
    """
    if request.method == "POST":
        form = BulkSubmitForm(request.POST, request.FILES)

        if form.is_valid():
            professor_nome = form.cleaned_data["professor_nome"]
            professor_email = form.cleaned_data["professor_email"]
            componente = form.cleaned_data["componente"]

            texto_questoes_bruto = form.cleaned_data.get("texto_questoes", "")
            arquivo_backup = request.FILES.get("arquivo_backup")

            questoes_criadas_count = 0
            questoes_ignoradas_count = 0

            # LÓGICA 1: RESTAURAR VIA ARQUIVO JSON
            if arquivo_backup:
                try:
                    dados_backup = json.load(arquivo_backup)

                    with transaction.atomic():
                        for item in dados_backup:
                            enunciado = item.get("enunciado")
                            if not enunciado:
                                questoes_ignoradas_count += 1
                                continue

                            nova_questao = Questao.objects.create(
                                professor_nome=professor_nome,
                                professor_email=professor_email,
                                componente=componente,
                                tipo_questao=item.get("tipo_questao", "RESPOSTA_UNICA"),
                                uso_prova=item.get("uso_prova", "INTEGRADA"),
                                status="PENDENTE",
                                texto_base=item.get("texto_base"),
                                enunciado=enunciado,
                                justificativa=item.get("justificativa"),
                                comentario_validacao=item.get("comentario_validacao"),
                            )

                            for alt in item.get("alternativas", []):
                                Alternativa.objects.create(
                                    questao=nova_questao,
                                    texto=alt.get("texto", "Texto não extraído"),
                                    eh_correta=alt.get("correta", False),
                                )

                            questoes_criadas_count += 1

                    messages.success(
                        request,
                        f"Backup importado com sucesso! {questoes_criadas_count} questão(ões) foram restauradas como 'Pendentes'.",
                    )
                    if questoes_ignoradas_count > 0:
                        messages.warning(
                            request,
                            f"{questoes_ignoradas_count} questão(ões) do backup foram ignoradas por erro de estrutura.",
                        )

                    return redirect("bulk_submit_form")

                except json.JSONDecodeError:
                    messages.error(request, "O arquivo selecionado não é um JSON válido.")
                    return render(request, "bulk_submit/bulk_submit_form.html", {"form": form})
                except Exception:
                    logger.exception("Backup import failed")
                    messages.error(
                        request, "Não foi possível importar. Nenhuma questão deste lote foi salva."
                    )
                    return render(request, "bulk_submit/bulk_submit_form.html", {"form": form})

            # LÓGICA 2: PROCESSAR TEXTO COM IA
            elif texto_questoes_bruto.strip():
                try:
                    resultado_ia = processar_texto_com_ia(texto_questoes_bruto)

                    if isinstance(resultado_ia, dict) and "erro" in resultado_ia:
                        messages.error(request, f"Erro ao processar o texto com a IA: {resultado_ia['erro']}")
                        return render(request, "bulk_submit/bulk_submit_form.html", {"form": form})
                    elif not isinstance(resultado_ia, list) or not resultado_ia:
                        messages.error(
                            request, "A IA não conseguiu extrair nenhuma questão válida do texto fornecido."
                        )
                        return render(request, "bulk_submit/bulk_submit_form.html", {"form": form})
                    else:
                        dados_estruturados = resultado_ia

                    with transaction.atomic():
                        for dados_q in dados_estruturados:
                            enunciado_extraido = dados_q.get("enunciado")
                            if not enunciado_extraido:
                                messages.warning(
                                    request,
                                    f"Questão ignorada por falta total de enunciado: {str(dados_q)[:100]}...",
                                )
                                questoes_ignoradas_count += 1
                                continue

                            valid_types = [choice[0] for choice in Questao.TIPO_QUESTAO_CHOICES]
                            tipo_q = dados_q.get("tipo_questao", "RESPOSTA_UNICA")
                            if tipo_q not in valid_types:
                                tipo_q = "RESPOSTA_UNICA"

                            nova_questao = Questao.objects.create(
                                professor_nome=professor_nome,
                                professor_email=professor_email,
                                componente=componente,
                                tipo_questao=tipo_q,
                                texto_base=dados_q.get("texto_base"),
                                enunciado=enunciado_extraido,
                                proposicao_dois=dados_q.get("proposicao_dois")
                                if tipo_q == "ASSERCAO_RAZAO"
                                else None,
                                justificativa=dados_q.get("justificativa"),
                                status="PENDENTE",
                            )

                            alternativas_data = dados_q.get("alternativas", [])
                            for dados_alt in alternativas_data:
                                Alternativa.objects.create(
                                    questao=nova_questao,
                                    texto=dados_alt.get("texto", "Texto não extraído"),
                                    eh_correta=dados_alt.get("eh_correta", False),
                                )
                            questoes_criadas_count += 1

                    if questoes_criadas_count > 0:
                        messages.success(
                            request,
                            f"{questoes_criadas_count} questão(ões) foram processadas e salvas via IA.",
                        )
                    if questoes_ignoradas_count > 0:
                        messages.warning(
                            request,
                            f"{questoes_ignoradas_count} questão(ões) foram ignoradas por falta de enunciado.",
                        )
                    if questoes_criadas_count == 0 and questoes_ignoradas_count == 0:
                        messages.error(request, "Nenhuma questão foi criada.")

                    return redirect("bulk_submit_form")

                except Exception:
                    logger.exception("Bulk AI import failed")
                    messages.error(request, "Não foi possível processar o lote. Nenhuma questão foi salva.")

            else:
                messages.error(
                    request,
                    "Você precisa enviar um arquivo JSON de backup OU colar o texto das questões para a IA processar.",
                )

        else:
            messages.warning(request, "Por favor, corrija os erros no formulário.")

    else:
        form = BulkSubmitForm()

    return render(request, "bulk_submit/bulk_submit_form.html", {"form": form})


# ==========================================
# VIEW 2: UPLOAD DA GRADE CURRICULAR (CSV)
# ==========================================
# ==========================================
# VIEW 2: UPLOAD DA GRADE CURRICULAR (CSV)
# ==========================================
@staff_member_required
def upload_grade_csv_view(request):
    """
    Lê um arquivo CSV para cadastrar Períodos e Componentes em lote.
    Inclui sistema de auto-correção para ficheiros CSV mal formatados.
    """
    if request.method == "POST":
        form = UploadGradeCSVForm(request.POST, request.FILES)
        if form.is_valid():
            semestre = form.cleaned_data["semestre"]
            arquivo_csv = request.FILES["arquivo_csv"]

            try:
                # 1. Lê o ficheiro completo e limpa marcas ocultas (BOM) do Excel
                texto_bruto = arquivo_csv.file.read().decode("utf-8-sig", errors="replace")

                # 2. A MÁGICA: Deteta se o ficheiro veio "colado" (sem quebras de linha)
                if "\n" not in texto_bruto and "\r" not in texto_bruto:
                    # Força uma quebra de linha antes de qualquer '"1º', '"2º', etc.
                    texto_bruto = re.sub(r'(?<=\w|\d|")(?="\d+º PERÍODO)', "\n", texto_bruto)

                # 3. Transforma o texto corrigido para o leitor CSV
                csv_file = StringIO(texto_bruto)
                leitor = csv.reader(csv_file, delimiter=",")

                next(leitor, None)  # Pula o cabeçalho

                periodos_criados = 0
                componentes_criados = 0
                componentes_atualizados = 0

                with transaction.atomic():
                    for linha in leitor:
                        if not linha or len(linha) < 4:
                            continue

                        nome_periodo = linha[0].strip()
                        nome_componente = linha[1].strip()

                        try:
                            meta_questoes = int(linha[3].strip())
                        except ValueError:
                            meta_questoes = 5

                        periodo_obj, criado_p = Periodo.objects.get_or_create(
                            nome=nome_periodo, semestre=semestre
                        )
                        if criado_p:
                            periodos_criados += 1

                        componente_obj, criado_c = ComponenteCurricular.objects.get_or_create(
                            nome=nome_componente,
                            periodo=periodo_obj,
                            defaults={"numero_questoes_prova": meta_questoes},
                        )

                        if criado_c:
                            componentes_criados += 1
                        else:
                            if componente_obj.numero_questoes_prova != meta_questoes:
                                componente_obj.numero_questoes_prova = meta_questoes
                                componente_obj.save()
                                componentes_atualizados += 1

                messages.success(
                    request,
                    f"Sucesso! {periodos_criados} novos Períodos e {componentes_criados} novos Componentes cadastrados no semestre {semestre.nome}. ({componentes_atualizados} metas atualizadas).",
                )
                return redirect("upload_grade_csv")

            except Exception:
                logger.exception("Curriculum import failed")
                messages.error(
                    request, "Não foi possível importar a grade. Nenhuma alteração deste lote foi salva."
                )

    else:
        form = UploadGradeCSVForm()

    return render(request, "bulk_submit/upload_grade_form.html", {"form": form})
