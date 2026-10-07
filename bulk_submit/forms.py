# bulk_submit/forms.py

from django import forms

from questoes.models import ComponenteCurricular, Periodo, Semestre


# ==========================================
# FORMULÁRIO 1: SUBMISSÃO DE QUESTÕES
# ==========================================
class BulkSubmitForm(forms.Form):
    # Campos de identificação
    professor_nome = forms.CharField(
        label="Nome Completo do Professor",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "w-full text-base px-4 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary",
                "placeholder": "Seu nome completo",
            }
        ),
    )
    professor_email = forms.EmailField(
        label="E-mail para Notificação",
        widget=forms.EmailInput(
            attrs={
                "class": "w-full text-base px-4 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary",
                "placeholder": "Seu melhor e-mail para contato",
            }
        ),
    )

    # Seletores de Período e Componente
    periodo = forms.ModelChoiceField(
        queryset=Periodo.objects.all().order_by("nome"),
        label="Selecione o Período",
        empty_label="--- Selecione um Período ---",
        widget=forms.Select(
            attrs={
                "class": "w-full text-base px-4 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary",
                "id": "id_periodo_selector_bulk",
            }
        ),
    )
    componente = forms.ModelChoiceField(
        queryset=ComponenteCurricular.objects.none(),
        label="Selecione o Componente Curricular",
        empty_label="--- Selecione um Componente ---",
        widget=forms.Select(
            attrs={
                "class": "w-full text-base px-4 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary",
                "id": "id_componente_selector_bulk",
            }
        ),
    )

    # Campo de Upload de Backup
    arquivo_backup = forms.FileField(
        required=False,
        label="Restaurar de um Backup (.json)",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100",
                "accept": ".json",
            }
        ),
        help_text="Selecione o arquivo JSON gerado pelo seu repositório do GitHub.",
    )

    # Caixa de texto para colar as questões (IA)
    texto_questoes = forms.CharField(
        required=False,
        label="Ou cole aqui o texto das questões para a IA processar",
        widget=forms.Textarea(
            attrs={
                "class": "w-full text-base px-4 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary custom-scrollbar",
                "rows": 10,
                "placeholder": "Cole o texto contendo uma ou mais questões...",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep legacy unassigned periods, but never submit into an archived semester.
        self.fields["periodo"].queryset = (
            Periodo.objects.filter(ativo=True).exclude(semestre__ativo=False).order_by("nome")
        )
        self.fields["componente"].queryset = ComponenteCurricular.objects.none()

        if "periodo" in self.data:
            try:
                periodo_id = int(self.data.get("periodo"))
                self.fields["componente"].queryset = (
                    ComponenteCurricular.objects.filter(
                        periodo_id=periodo_id, periodo__ativo=True, consolidado_em__isnull=True
                    )
                    .exclude(periodo__semestre__ativo=False)
                    .order_by("nome")
                )
            except (ValueError, TypeError):
                pass


# ==========================================
# FORMULÁRIO 2: UPLOAD DA GRADE (CSV)
# ==========================================
class UploadGradeCSVForm(forms.Form):
    semestre = forms.ModelChoiceField(
        queryset=Semestre.objects.filter(ativo=True),
        label="Selecione o Semestre de Destino",
        empty_label="--- Selecione o Semestre ---",
        widget=forms.Select(
            attrs={
                "class": "w-full text-base px-4 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-primary"
            }
        ),
    )
    arquivo_csv = forms.FileField(
        label="Arquivo CSV da Grade Curricular",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-blue-50 file:text-blue-700",
                "accept": ".csv",
            }
        ),
    )
