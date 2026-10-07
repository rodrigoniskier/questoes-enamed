# questoes/admin.py

import json

from django import forms
from django.contrib import admin, messages
from django.db import models
from django.db.models import Count, Q
from django.forms import Textarea
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse
from django.utils.html import format_html

# Modelos e utils
from .models import AIPrompt, Alternativa, ComponenteCurricular, Periodo, Questao, Semestre
from .utils import avaliar_questao_com_ia, enviar_email_status


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


class QuestaoAdminForm(forms.ModelForm):
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

    # Adicionado 'get_semestre' na visualização e o filtro superior no menu lateral
    list_display = (
        "id",
        "enunciado_curto",
        "get_componente",
        "get_periodo",
        "get_semestre",
        "tipo_questao",
        "uso_prova",
        "status_colorido",
    )
    list_filter = (
        "status",
        "componente__periodo__semestre",
        "componente__periodo",
        "componente",
        "tipo_questao",
        "uso_prova",
    )
    search_fields = ("enunciado", "texto_base", "professor_nome", "professor_email")
    readonly_fields = ("professor_nome", "professor_email")

    fieldsets = (
        ("Identificação do Professor", {"fields": ("professor_nome", "professor_email")}),
        ("Classificação", {"fields": ("componente", "tipo_questao", "uso_prova", "status")}),
        ("Conteúdo da Questão", {"fields": ("texto_base", "imagem", "enunciado", "proposicao_dois")}),
        ("Gabarito e Feedback", {"fields": ("justificativa", "comentario_validacao")}),
    )

    actions = [
        "exportar_backup_json",
        "avaliar_com_ia",
        "aprovar_e_notificar",
        "reprovar_e_notificar",
        "marcar_como_pendente",
        "definir_uso_simulado",
        "definir_uso_residencia",
        "definir_uso_reposicao",
        "definir_uso_integrada",
        "definir_uso_ambas",
    ]

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
                "professor": q.professor_nome,
                "email": q.professor_email,
                "texto_base": q.texto_base,
                "enunciado": q.enunciado,
                "justificativa": q.justificativa,
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

    def _set_status_and_notify(self, request, queryset, status):
        delivered = failed = 0
        for question in queryset:
            if question.status != status:
                question.status = status
                question.save(update_fields=["status"])
            try:
                enviar_email_status(question)
                delivered += 1
            except Exception:
                failed += 1
                self.message_user(
                    request,
                    f"Questão {question.pk}: status salvo, mas o e-mail falhou. Execute a ação novamente para tentar reenviar.",
                    level=messages.WARNING,
                )
        self.message_user(
            request, f"{delivered} notificação(ões) enviada(s); {failed} falha(s). Os status foram salvos."
        )

    @admin.action(description="Aprovar selecionadas e notificar professor")
    def aprovar_e_notificar(self, request, queryset):
        self._set_status_and_notify(request, queryset, "APROVADA")

    @admin.action(description="Reprovar selecionadas e notificar professor")
    def reprovar_e_notificar(self, request, queryset):
        self._set_status_and_notify(request, queryset, "REPROVADA")

    @admin.action(description="Marcar selecionadas como Pendente")
    def marcar_como_pendente(self, request, queryset):
        queryset.update(status="PENDENTE")
        self.message_user(request, "Questões marcadas como PENDENTE.")

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
        cores = {"APROVADA": "green", "REPROVADA": "red", "PENDENTE": "orange"}
        cor = cores.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>', cor, obj.get_status_display()
        )


# ==========================================
# NOVO ADMIN DE SEMESTRES
# ==========================================
@admin.register(Semestre)
class SemestreAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo", "total_questoes", "total_aprovadas", "total_pendentes")
    list_filter = ("ativo",)
    search_fields = ("nome",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _total=Count("periodos__componentes__questoes", distinct=True),
            _aprovadas=Count(
                "periodos__componentes__questoes",
                filter=Q(periodos__componentes__questoes__status="APROVADA"),
                distinct=True,
            ),
            _pendentes=Count(
                "periodos__componentes__questoes",
                filter=Q(periodos__componentes__questoes__status="PENDENTE"),
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
        "meta_questoes",
        "total_enviadas",
        "total_aprovadas",
        "total_pendentes",
    )
    list_filter = ("semestre", "nome")
    search_fields = ("nome",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _total=Count("componentes__questoes", distinct=True),
            _aprovadas=Count(
                "componentes__questoes", filter=Q(componentes__questoes__status="APROVADA"), distinct=True
            ),
            _pendentes=Count(
                "componentes__questoes", filter=Q(componentes__questoes__status="PENDENTE"), distinct=True
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
        "get_semestre",
        "meta_questoes",
        "total_enviadas",
        "total_aprovadas",
        "total_pendentes",
    )
    list_filter = ("periodo__semestre", "periodo")
    search_fields = ("nome",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _total=Count("questoes", distinct=True),
            _aprovadas=Count("questoes", filter=Q(questoes__status="APROVADA"), distinct=True),
            _pendentes=Count("questoes", filter=Q(questoes__status="PENDENTE"), distinct=True),
        )

    @admin.display(description="Semestre")
    def get_semestre(self, obj):
        return obj.periodo.semestre.nome if obj.periodo.semestre else "-"

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
