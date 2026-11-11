# questoes/admin.py

import json
import logging
import uuid

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, models, transaction
from django.db.models import Count, Q
from django.forms import Textarea
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .avaliacao import registrar_decisao
from .exclusao import arquivar_questoes
from .feedback import texto_pedagogico
from .historico import contexto_historico

# Modelos e utils
from .importacao_admin import importar_view
from .models import (
    AIPrompt,
    Alternativa,
    ComponenteCurricular,
    ImportacaoQuestao,
    Periodo,
    Questao,
    QuestaoExcluida,
    Semestre,
)
from .multiple_choice import (
    gerar_alternativas_multipla_escolha,
    interpretar_combinacoes,
    linhas_combinacoes,
    validar_combinacoes,
)
from .submission import validate_alternatives
from .utils import avaliar_questao_com_ia

logger = logging.getLogger(__name__)


# ==========================================
# CONFIGURAÇÕES DOS FORMULÁRIOS INLINE
# ==========================================
class AlternativaInlineForm(forms.ModelForm):
    class Meta:
        model = Alternativa
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["texto"].required = False


class AlternativaFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        for form in self.forms:
            if not form.is_valid():
                continue
            if form.cleaned_data and not form.cleaned_data.get("DELETE"):
                texto = form.cleaned_data.get("texto")
                if not texto and form.instance.pk:
                    form.cleaned_data["DELETE"] = True
        if any(self.errors):
            return
        if self.instance.tipo_questao != "MULTIPLA_ESCOLHA":
            self.instance.combinacoes_personalizadas = []
            return
        afirmativas = [
            {
                "texto": form.cleaned_data.get("texto", ""),
                "eh_correta": form.cleaned_data.get("eh_correta", False),
            }
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and (form.cleaned_data.get("texto") or form.cleaned_data.get("eh_correta"))
        ]
        validate_alternatives(afirmativas, "MULTIPLA_ESCOLHA", 5)
        if self.instance.combinacoes_personalizadas:
            validar_combinacoes(self.instance.combinacoes_personalizadas, afirmativas)


class AlternativaInline(admin.TabularInline):
    model = Alternativa
    form = AlternativaInlineForm
    formset = AlternativaFormSet
    extra = 0
    min_num = 0
    # ALTERAÇÃO 1: Removido 'cols' e aumentado 'rows' para 5.
    # Adicionado estilo 'width: 100%' para garantir que a caixa se ajuste à tela.
    formfield_overrides = {
        models.CharField: {"widget": Textarea(attrs={"rows": 5, "style": "width: 100%;"})},
    }


class CombinacoesWidget(forms.Textarea):
    template_name = "admin/questoes/questao/combinacoes_widget.html"


class QuestaoAdminForm(forms.ModelForm):
    combinacoes_personalizadas = forms.CharField(
        required=False,
        max_length=200,
        label="Combinações finais A–D",
        widget=CombinacoesWidget(attrs={"rows": 5, "style": "width: 100%;"}),
        help_text="Uma combinação por linha, usando I, II, III, IV ou V separados por vírgulas. Deixe vazio para geração automática. As letras podem mudar na prova para equilibrar o gabarito; as combinações serão preservadas.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["comentario_validacao"] = texto_pedagogico(self.initial.get("comentario_validacao"))
        if "combinacoes_personalizadas" in self.fields:
            self.initial["combinacoes_personalizadas"] = linhas_combinacoes(
                self.instance.combinacoes_personalizadas or []
            )
            if self.instance.pk and self.instance.tipo_questao == "MULTIPLA_ESCOLHA":
                options = gerar_alternativas_multipla_escolha(self.instance)
                if all("indices" in option for option in options):
                    self.fields["combinacoes_personalizadas"].widget.attrs["data-combinacoes"] = (
                        linhas_combinacoes([o["indices"] for o in options])
                    )

    def clean_combinacoes_personalizadas(self):
        return interpretar_combinacoes(self.cleaned_data["combinacoes_personalizadas"])

    class Meta:
        model = Questao
        fields = "__all__"
        widgets = {
            # ALTERAÇÃO 2: Removido 'cols' de todos os campos.
            # Aumentado 'rows' para dar mais altura.
            # Adicionado estilo para evitar overflow horizontal.
            "texto_base": Textarea(attrs={"rows": 10, "style": "width: 100%;"}),
            "enunciado": Textarea(attrs={"rows": 8, "style": "width: 100%;"}),
            "proposicao_dois": Textarea(attrs={"rows": 8, "style": "width: 100%;"}),
            "justificativa": Textarea(attrs={"rows": 10, "style": "width: 100%;"}),
        }


# ==========================================
# ADMIN DE QUESTÕES (COM FILTRO DE SEMESTRE)
# ==========================================
class QuestaoAdmin(admin.ModelAdmin):
    form = QuestaoAdminForm
    inlines = [AlternativaInline]
    delete_confirmation_template = "admin/questoes/questao/confirmar_exclusao.html"
    delete_selected_confirmation_template = delete_confirmation_template

    def get_deleted_objects(self, objs, request):
        # O fluxo apenas retira questões do banco ativo: nenhum registro
        # relacionado será apagado. A permissão exigida continua sendo a da questão.
        questoes = list(objs)
        permissoes = {
            self.opts.verbose_name for questao in questoes if not self.has_delete_permission(request, questao)
        }
        return (
            [str(questao) for questao in questoes],
            {self.opts.verbose_name_plural: len(questoes)},
            permissoes,
            [],
        )

    def delete_model(self, request, obj):
        arquivar_questoes(Questao.objects.filter(pk=obj.pk), request.user)

    def delete_queryset(self, request, queryset):
        arquivar_questoes(queryset, request.user)

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except DatabaseError as erro:
            logger.error("Question archival failed: %s", type(erro).__name__)
            self.message_user(
                request,
                "Não foi possível concluir a exclusão. A questão foi preservada. Tente novamente.",
                messages.ERROR,
            )
            return redirect("admin:questoes_questao_delete", object_id)

    @admin.action(description="Excluir questões selecionadas", permissions=["delete"])
    def delete_selected(self, request, queryset):
        # Inclui o registro administrativo e o arquivamento na mesma transação.
        try:
            with transaction.atomic():
                return delete_selected(self, request, queryset)
        except DatabaseError as erro:
            logger.error("Question batch archival failed: %s", type(erro).__name__)
            self.message_user(
                request,
                "Não foi possível concluir a exclusão. As questões foram preservadas. Tente novamente.",
                messages.ERROR,
            )

    # Adicionado 'get_semestre' na visualização e o filtro superior no menu lateral
    list_display = (
        "id",
        "enunciado_curto",
        "data_envio",
        "get_componente",
        "get_periodo",
        "get_semestre",
        "tipo_questao",
        "uso_prova",
        "status_colorido",
    )
    list_filter = (
        "status",
        "enviada_em",
        "componente__periodo__semestre",
        "componente__periodo",
        "componente",
        "tipo_questao",
        "uso_prova",
    )
    search_fields = ("enunciado", "texto_base", "professor_nome", "professor_email")
    readonly_fields = (
        "professor_nome",
        "professor_email",
        "data_envio",
        "status",
        "registrar_parecer",
        "historico_alteracoes",
        "combinacoes_atuais",
    )

    fieldsets = (
        ("Identificação do Professor", {"fields": ("professor_nome", "professor_email", "data_envio")}),
        (
            "Classificação",
            {"fields": ("componente", "tipo_questao", "uso_prova", "status", "registrar_parecer")},
        ),
        ("Conteúdo da Questão", {"fields": ("texto_base", "imagem", "enunciado", "proposicao_dois")}),
        ("Gabarito e Feedback", {"fields": ("justificativa", "comentario_validacao")}),
        (
            "Histórico de Alterações",
            {"fields": ("historico_alteracoes",), "classes": ("historico-tab",)},
        ),
    )

    actions = [
        "delete_selected",
        "exportar_backup_json",
        "avaliar_com_ia",
        "aprovar_e_notificar",
        "aprovar_com_sugestoes",
        "precisa_de_revisoes_e_notificar",
        "marcar_como_pendente",
        "definir_uso_simulado",
        "definir_uso_residencia",
        "definir_uso_reposicao",
        "definir_uso_integrada",
        "definir_uso_ambas",
    ]

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if obj and obj.tipo_questao == "MULTIPLA_ESCOLHA":
            fieldsets.insert(
                -1,
                (
                    "Combinações A–D",
                    {
                        "fields": ("combinacoes_atuais", "combinacoes_personalizadas"),
                        "classes": ("combinacoes-tab",),
                    },
                ),
            )
        return fieldsets

    @admin.display(description="Combinações utilizadas")
    def combinacoes_atuais(self, obj):
        return render_to_string(
            "admin/questoes/questao/combinacoes_preview.html",
            {
                "opcoes": gerar_alternativas_multipla_escolha(obj),
            },
        )

    @admin.display(description="Data de envio", ordering="enviada_em")
    def data_envio(self, obj):
        if obj.enviada_em is None:
            return "Não registrada"
        return timezone.localtime(obj.enviada_em).strftime("%d/%m/%Y %H:%M")

    @admin.display(description="Comparação das versões")
    def historico_alteracoes(self, obj):
        if not obj or not obj.pk:
            return "Salve a questão para consultar seu histórico."
        return render_to_string("admin/questoes/questao/historico.html", contexto_historico(obj))

    @admin.action(description="💾 Exportar Backup Completo (JSON)")
    def exportar_backup_json(self, request, queryset):
        data = []
        for q in queryset:
            item = {
                "id_original": q.id,
                # Incluído semestre no JSON para o seu App Web no GitHub
                "semestre": q.componente.periodo.semestre.nome if q.componente.periodo.semestre else None,
                "periodo": q.componente.periodo.nome,
                "componente": q.componente.nome,
                "tipo_questao": q.tipo_questao,
                "uso_prova": q.uso_prova,
                "status": q.status,
                "status_descricao": q.get_status_display(),
                "professor": q.professor_nome,
                "email": q.professor_email,
                "texto_base": q.texto_base,
                "enunciado": q.enunciado,
                "justificativa": q.justificativa,
                "combinacoes_personalizadas": q.combinacoes_personalizadas,
                "comentario_validacao": q.comentario_validacao,
                "alternativas": [
                    {"texto": alt.texto, "correta": alt.eh_correta} for alt in q.alternativas.all()
                ],
            }
            data.append(item)

        response_content = json.dumps(data, indent=4, ensure_ascii=False)
        response = HttpResponse(response_content, content_type="application/json")
        response["Content-Disposition"] = 'attachment; filename="backup_questoes_medicina.json"'
        return response

    @admin.action(description="⭐ Avaliar Questões Selecionadas com IA")
    def avaliar_com_ia(self, request, queryset):
        sucessos = 0
        erros = 0
        for questao in queryset:
            resultado = avaliar_questao_com_ia(questao)
            if "erro" in resultado:
                erros += 1
                self.message_user(
                    request, f"Erro na questão ID {questao.id}: {resultado['erro']}", level=messages.ERROR
                )
            else:
                questao.comentario_validacao = resultado["relatorio"]
                questao.save()
                sucessos += 1
        if sucessos > 0:
            self.message_user(request, f"{sucessos} questão(ões) avaliada(s).", level=messages.SUCCESS)

    def get_urls(self):
        return [
            path(
                "<int:pk>/parecer/",
                self.admin_site.admin_view(self.parecer_view),
                name="questoes_questao_parecer",
            )
        ] + super().get_urls()

    @admin.display(description="Parecer institucional")
    def registrar_parecer(self, obj):
        if not obj or not obj.pk:
            return "Salve a questão para registrar um parecer."
        return format_html(
            '<a href="{}">Registrar ou reavaliar parecer</a>',
            reverse("admin:questoes_questao_parecer", args=[obj.pk]),
        )

    def parecer_view(self, request, pk):
        from pre_auditoria.forms import ParecerInstitucionalForm
        from pre_auditoria.services import (
            POLICY_VERSION,
            executar_pre_auditoria,
            fingerprint_questao,
            tem_pendencias_obrigatorias,
        )

        question = get_object_or_404(self.get_queryset(request), pk=pk)
        if not self.has_change_permission(request, question):
            raise PermissionDenied
        report = question.pareceres_pre_auditoria.filter(arquivado_em__isnull=True).first()
        question.approval_exception_needed = bool(
            report
            and report.fingerprint == fingerprint_questao(question)
            and report.indicadores.get("politica") == POLICY_VERSION
            and tem_pendencias_obrigatorias(report)
        )
        latest = question.decisoes_pre_auditoria.first()
        if request.method == "POST" and request.POST.get("verificar_estrutura"):
            executar_pre_auditoria(question, revisor=request.user, semantica=False)
            self.message_user(
                request, "Verificação estrutural atualizada. A decisão permanece sob sua responsabilidade."
            )
            return redirect("admin:questoes_questao_parecer", pk=question.pk)
        form = ParecerInstitucionalForm(
            request.POST if request.method == "POST" else None,
            initial={
                "fingerprint": fingerprint_questao(question),
                "request_id": uuid.uuid4(),
                "parecer": texto_pedagogico(latest.justificativa) if latest else "",
            },
        )
        if request.method == "POST" and form.is_valid():
            try:
                registrar_decisao(
                    question.pk,
                    request.user,
                    form.cleaned_data["classificacao"],
                    form.cleaned_data["parecer"],
                    fingerprint=form.cleaned_data["fingerprint"],
                    request_id=form.cleaned_data["request_id"],
                    base_url=request.build_absolute_uri("/"),
                )
            except ValidationError as error:
                form.add_error(None, error)
            except DatabaseError:
                form.add_error(
                    None, "Não foi possível registrar a decisão. Seu texto foi preservado; tente novamente."
                )
            else:
                self.message_user(request, "Parecer institucional registrado.")
                return redirect("admin:questoes_questao_change", question.pk)
        return TemplateResponse(
            request,
            "admin/questoes/questao/parecer.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": f"Parecer da questão {question.pk}",
                "questao": question,
                "form": form,
            },
            status=400 if form.is_bound else 200,
        )

    def save_model(self, request, obj, form, change):
        from pre_auditoria.services import snapshot_questao

        obj._antes_parecer = snapshot_questao(Questao.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        from pre_auditoria.models import AlteracaoPreAuditoria
        from pre_auditoria.services import executar_pre_auditoria, snapshot_questao

        super().save_related(request, form, formsets, change)
        question = form.instance
        before = getattr(question, "_antes_parecer", None)
        question.refresh_from_db()
        after = snapshot_questao(question)
        if before is not None and before != after:
            AlteracaoPreAuditoria.objects.create(
                questao=question,
                revisor=request.user,
                revisor_nome=request.user.get_username(),
                antes=before,
                depois=after,
            )
            executar_pre_auditoria(question, revisor=request.user, semantica=False)

    def _set_status_and_notify(self, request, queryset, status):
        count = 0
        for question in queryset:
            try:
                registrar_decisao(
                    question.pk,
                    request.user,
                    status,
                    question.comentario_validacao or "",
                    base_url=request.build_absolute_uri("/"),
                )
            except ValidationError as error:
                self.message_user(
                    request, f"Questão {question.pk}: " + "; ".join(error.messages), level=messages.ERROR
                )
            else:
                count += 1
        self.message_user(
            request, f"{count} decisão(ões) registrada(s). Consulte o histórico para o resultado do envio."
        )

    @admin.action(description="Aprovar selecionadas e notificar professor", permissions=["change"])
    def aprovar_e_notificar(self, request, queryset):
        self._set_status_and_notify(request, queryset, "APROVADA")

    @admin.action(description="Aprovar com sugestões (usar orientações registradas)", permissions=["change"])
    def aprovar_com_sugestoes(self, request, queryset):
        self._set_status_and_notify(request, queryset, "APROVADA_SUGESTOES")

    @admin.action(
        description="Marcar como Precisa de Revisões e notificar (usar orientações registradas)",
        permissions=["change"],
    )
    def precisa_de_revisoes_e_notificar(self, request, queryset):
        self._set_status_and_notify(request, queryset, "REPROVADA")

    @admin.action(description="Devolver à situação Pendente de Validação", permissions=["change"])
    def marcar_como_pendente(self, request, queryset):
        self._set_status_and_notify(request, queryset, "PENDENTE")

    # --- AÇÕES DE USO DE PROVA (Mantidas) ---
    @admin.action(description="Uso: SIMULADO")
    def definir_uso_simulado(self, request, queryset):
        queryset.update(uso_prova="SIMULADO")

    @admin.action(description="Uso: RESIDÊNCIA")
    def definir_uso_residencia(self, request, queryset):
        queryset.update(uso_prova="RESIDENCIA")

    @admin.action(description="Uso: INTEGRADA")
    def definir_uso_integrada(self, request, queryset):
        queryset.update(uso_prova="INTEGRADA")

    @admin.action(description="Uso: AMBAS (Integrada/Reposição)")
    def definir_uso_ambas(self, request, queryset):
        queryset.update(uso_prova="AMBAS")

    # --- EXIBIÇÃO NO PAINEL ---
    @admin.display(description="Enunciado")
    def enunciado_curto(self, obj):
        return (obj.enunciado[:75] + "...") if len(obj.enunciado) > 75 else obj.enunciado

    @admin.display(description="Semestre", ordering="componente__periodo__semestre__nome")
    def get_semestre(self, obj):
        return obj.componente.periodo.semestre.nome if obj.componente.periodo.semestre else "-"

    @admin.display(description="Período", ordering="componente__periodo__nome")
    def get_periodo(self, obj):
        return obj.componente.periodo.nome

    @admin.display(description="Componente", ordering="componente__nome")
    def get_componente(self, obj):
        return obj.componente.nome

    @admin.display(description="Status")
    def status_colorido(self, obj):
        cores = {"APROVADA_SUGESTOES": "teal", "APROVADA": "green", "REPROVADA": "red", "PENDENTE": "orange"}
        cor = cores.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>', cor, obj.get_status_display()
        )


# ==========================================
# NOVO ADMIN DE SEMESTRES
# ==========================================
@admin.register(Semestre)
class SemestreAdmin(admin.ModelAdmin):
    list_display = ("nome", "situacao", "ativo", "total_questoes", "total_aprovadas", "total_pendentes")
    list_filter = ("ativo",)
    search_fields = ("nome",)

    @admin.display(description="Situação")
    def situacao(self, obj):
        return "Ativo" if obj.ativo else "Arquivado"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _total=Count(
                "periodos__componentes__questoes",
                filter=Q(periodos__componentes__questoes__excluida_em__isnull=True),
                distinct=True,
            ),
            _aprovadas=Count(
                "periodos__componentes__questoes",
                filter=Q(
                    periodos__componentes__questoes__status__in=Questao.STATUS_APROVADOS,
                    periodos__componentes__questoes__excluida_em__isnull=True,
                ),
                distinct=True,
            ),
            _pendentes=Count(
                "periodos__componentes__questoes",
                filter=Q(
                    periodos__componentes__questoes__status="PENDENTE",
                    periodos__componentes__questoes__excluida_em__isnull=True,
                ),
                distinct=True,
            ),
        )

    @admin.display(description="Total Questões", ordering="_total")
    def total_questoes(self, obj):
        return obj._total

    @admin.display(description="Aprovadas ✅", ordering="_aprovadas")
    def total_aprovadas(self, obj):
        return obj._aprovadas

    @admin.display(description="Pendentes ⌛", ordering="_pendentes")
    def total_pendentes(self, obj):
        return obj._pendentes


# ==========================================
# ADMIN DE PERÍODOS E COMPONENTES
# ==========================================
class PeriodoAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "semestre",
        "ativo",
        "meta_questoes",
        "total_enviadas",
        "total_aprovadas",
        "total_pendentes",
    )
    list_filter = ("semestre", "ativo", "nome")
    search_fields = ("nome",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _total=Count(
                "componentes__questoes",
                filter=Q(componentes__questoes__excluida_em__isnull=True),
                distinct=True,
            ),
            _aprovadas=Count(
                "componentes__questoes",
                filter=Q(
                    componentes__questoes__status__in=Questao.STATUS_APROVADOS,
                    componentes__questoes__excluida_em__isnull=True,
                ),
                distinct=True,
            ),
            _pendentes=Count(
                "componentes__questoes",
                filter=Q(
                    componentes__questoes__status="PENDENTE", componentes__questoes__excluida_em__isnull=True
                ),
                distinct=True,
            ),
        )

    @admin.display(description="Meta")
    def meta_questoes(self, obj):
        return sum(c.numero_questoes_prova for c in obj.componentes.all())

    @admin.display(description="Enviadas", ordering="_total")
    def total_enviadas(self, obj):
        return obj._total

    @admin.display(description="Aprovadas ✅", ordering="_aprovadas")
    def total_aprovadas(self, obj):
        return obj._aprovadas

    @admin.display(description="Pendentes ⌛", ordering="_pendentes")
    def total_pendentes(self, obj):
        return obj._pendentes


class ComponenteCurricularAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "periodo",
        "consolidado_em",
        "get_semestre",
        "meta_questoes",
        "total_enviadas",
        "total_integrada",
        "total_reposicao",
        "total_integrada_reposicao",
        "total_aprovadas",
        "total_pendentes",
    )
    list_filter = ("periodo__semestre", "periodo__ativo", "periodo")
    search_fields = ("nome",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _total=Count("questoes", filter=Q(questoes__excluida_em__isnull=True), distinct=True),
            _integrada=Count(
                "questoes",
                filter=Q(questoes__uso_prova="INTEGRADA", questoes__excluida_em__isnull=True),
                distinct=True,
            ),
            _reposicao=Count(
                "questoes",
                filter=Q(questoes__uso_prova="REPOSICAO", questoes__excluida_em__isnull=True),
                distinct=True,
            ),
            _integrada_reposicao=Count(
                "questoes",
                filter=Q(questoes__uso_prova="AMBAS", questoes__excluida_em__isnull=True),
                distinct=True,
            ),
            _aprovadas=Count(
                "questoes",
                filter=Q(questoes__status__in=Questao.STATUS_APROVADOS, questoes__excluida_em__isnull=True),
                distinct=True,
            ),
            _pendentes=Count(
                "questoes",
                filter=Q(questoes__status="PENDENTE", questoes__excluida_em__isnull=True),
                distinct=True,
            ),
        )

    @admin.display(description="Semestre")
    def get_semestre(self, obj):
        return obj.periodo.semestre.nome if obj.periodo.semestre else "-"

    @admin.display(description="Integrada", ordering="_integrada")
    def total_integrada(self, obj):
        return obj._integrada

    @admin.display(description="Reposição", ordering="_reposicao")
    def total_reposicao(self, obj):
        return obj._reposicao

    @admin.display(description="Integrada e reposição", ordering="_integrada_reposicao")
    def total_integrada_reposicao(self, obj):
        return obj._integrada_reposicao

    @admin.display(description="Meta")
    def meta_questoes(self, obj):
        return obj.numero_questoes_prova

    @admin.display(description="Enviadas", ordering="_total")
    def total_enviadas(self, obj):
        return obj._total

    @admin.display(description="Aprovadas ✅", ordering="_aprovadas")
    def total_aprovadas(self, obj):
        return obj._aprovadas

    @admin.display(description="Pendentes ⌛", ordering="_pendentes")
    def total_pendentes(self, obj):
        return obj._pendentes


@admin.register(AIPrompt)
class AIPromptAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "texto_prompt")


# Registro dos modelos
admin.site.register(Periodo, PeriodoAdmin)
admin.site.register(ComponenteCurricular, ComponenteCurricularAdmin)
admin.site.register(Questao, QuestaoAdmin)


class AlternativaHistoricaInline(admin.TabularInline):
    model = Alternativa
    readonly_fields = ("texto", "eh_correta")
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("questoes.view_questao") or request.user.has_perm(
            "questoes.change_questao"
        )


@admin.register(QuestaoExcluida)
class QuestaoExcluidaAdmin(QuestaoAdmin):
    actions = None
    inlines = [AlternativaHistoricaInline]
    list_display = ("id", "enunciado_curto", "excluida_em", "excluida_por", "get_componente")
    list_filter = ("excluida_em", "componente__periodo__semestre", "componente")
    readonly_fields = tuple(field.name for field in Questao._meta.fields) + QuestaoAdmin.readonly_fields

    def get_queryset(self, request):
        return QuestaoExcluida.todos_objetos.filter(excluida_em__isnull=False)

    def get_fieldsets(self, request, obj=None):
        return [("Exclusão do banco ativo", {"fields": ("excluida_em", "excluida_por")})] + list(
            super().get_fieldsets(request, obj)
        )

    def get_urls(self):
        return admin.ModelAdmin.get_urls(self)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("questoes.view_questao") or request.user.has_perm(
            "questoes.change_questao"
        )

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Parecer institucional")
    def registrar_parecer(self, obj):
        return "Questão excluída do banco ativo. Pareceres preservados somente para consulta."


@admin.register(ImportacaoQuestao)
class ImportacaoQuestaoAdmin(admin.ModelAdmin):
    list_display = ("origem_identificador", "copia", "receptor", "semestre", "responsavel", "criada_em")
    list_select_related = ("copia", "receptor__periodo", "semestre", "responsavel")
    list_filter = ("semestre", "receptor")
    readonly_fields = tuple(field.name for field in ImportacaoQuestao._meta.fields)
    change_list_template = "admin/questoes/importacaoquestao/change_list.html"
    actions = None

    def get_urls(self):
        return [
            path("importar/", self.admin_site.admin_view(self.importar), name="questoes_importar")
        ] + super().get_urls()

    def importar(self, request):
        return importar_view(request, admin_site=self.admin_site)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
