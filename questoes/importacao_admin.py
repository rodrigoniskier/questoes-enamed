import logging
from secrets import token_urlsafe

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .importacao import LIMITE_IMPORTACAO, importar_questoes, pode_importar, questoes_doador
from .importacao_forms import ContextoImportacaoForm, SelecaoImportacaoForm, assinar_estado
from .models import Questao

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def importar_view(request, *, admin_site):
    if not pode_importar(request.user):
        raise PermissionDenied
    chave = request.session.setdefault("questoes_importacao_chave", token_urlsafe(32))
    dados = request.POST if request.method == "POST" else request.GET
    contexto_form = ContextoImportacaoForm(dados or None, exigir_doador=request.method == "POST")
    contexto = {
        **admin_site.each_context(request),
        "title": "Importação de Questões entre Componentes",
        "contexto_form": contexto_form,
    }
    if (
        not contexto_form.is_bound
        or not contexto_form.is_valid()
        or not contexto_form.cleaned_data.get("doador")
    ):
        if request.method == "POST":
            contexto["erros"] = contexto_form.errors
        return render(
            request,
            "admin/questoes/importar.html",
            contexto,
            status=400 if request.method == "POST" else 200,
        )
    escolha = contexto_form.cleaned_data
    selecionadas = set()
    busca, status, pagina = "", "", 1
    resposta_status = 200
    selecao_valida = False
    if request.method == "POST":
        selecao_form = SelecaoImportacaoForm(dados, contexto=escolha, chave=chave)
        selecao_valida = selecao_form.is_valid()
        valores = selecao_form.cleaned_data
        if not selecao_valida:
            contexto["erros"] = selecao_form.errors
            if "selecionadas" not in valores:
                contexto["reiniciar"] = True
                return render(request, "admin/questoes/importar.html", contexto, status=400)
            resposta_status = 400
        selecionadas = valores["selecionadas"]
        busca, status = valores.get("busca", ""), valores.get("status", "")
        pagina = 1 if valores.get("acao") == "filtrar" else valores.get("pagina") or 1
        if selecao_valida and valores["acao"] == "confirmar":
            contexto.update(
                confirmacao=True,
                selecionadas=selecionadas,
                exemplos=Questao.objects.filter(pk__in=selecionadas).order_by("pk")[:50],
                estado=assinar_estado(
                    chave=chave,
                    contexto=escolha,
                    selecionadas=selecionadas,
                    visiveis=[],
                    fase="confirmacao",
                ),
                escolha=escolha,
            )
            return render(request, "admin/questoes/importar.html", contexto, status=resposta_status)
        if selecao_valida and valores["acao"] == "importar":
            try:
                resultado = importar_questoes(
                    usuario=request.user,
                    semestre_id=escolha["semestre"].pk,
                    doador_id=escolha["doador"].pk,
                    receptor_id=escolha["receptor"].pk,
                    questao_ids=selecionadas,
                )
            except ValidationError as exc:
                contexto["erros"] = exc.messages
                contexto["reiniciar"] = True
                return render(request, "admin/questoes/importar.html", contexto, status=400)
            except (DatabaseError, OSError):
                logger.exception("Importação revertida; responsável=%s", request.user.pk)
                contexto["erros"] = [
                    "Não foi possível concluir a importação. Nenhuma questão foi importada. "
                    "Tente novamente; se persistir, contate o administrador."
                ]
                contexto.update(
                    confirmacao=True,
                    escolha=escolha,
                    selecionadas=selecionadas,
                    exemplos=[],
                    estado=dados["estado"],
                )
                return render(request, "admin/questoes/importar.html", contexto, status=503)
            messages.success(
                request,
                f"{resultado.importadas} importadas; {resultado.ignoradas} ignoradas "
                "(já importadas); 0 erros. As cópias estão pendentes de validação.",
            )
            contexto["resultado"] = resultado
            return render(request, "admin/questoes/importar.html", contexto, status=resposta_status)
    questoes = questoes_doador(escolha["doador"], escolha["semestre"].pk).order_by("pk")
    if busca:
        questoes = questoes.filter(Q(enunciado__icontains=busca) | Q(texto_base__icontains=busca))
    if status:
        questoes = questoes.filter(status=status)
    if selecao_valida and valores["acao"] == "limpar_todas":
        selecionadas = set()
    elif selecao_valida and valores["acao"] == "selecionar_filtradas":
        novas = set(questoes.values_list("pk", flat=True)[: LIMITE_IMPORTACAO + 1])
        if len(novas | selecionadas) > LIMITE_IMPORTACAO:
            contexto["erros"] = [f"A seleção excede {LIMITE_IMPORTACAO} questões. Refine o filtro."]
        else:
            selecionadas |= novas
    pagina_obj = Paginator(questoes, 25).get_page(pagina)
    visiveis = [q.pk for q in pagina_obj.object_list]
    contexto.update(
        escolha=escolha,
        selecionadas=selecionadas,
        pagina_obj=pagina_obj,
        busca=busca,
        status_filtro=status,
        status_choices=Questao.STATUS_CHOICES,
        selecionadas_visiveis=len(selecionadas.intersection(visiveis)),
        estado=assinar_estado(chave=chave, contexto=escolha, selecionadas=selecionadas, visiveis=visiveis),
    )
    return render(request, "admin/questoes/importar.html", contexto, status=resposta_status)
