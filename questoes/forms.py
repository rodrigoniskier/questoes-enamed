# questoes/forms.py

from django import forms

from .models import Alternativa, Questao

# ============================================================================
# CLASSES CSS REUTILIZÁVEIS
# ============================================================================

text_input_classes = "w-full text-base px-4 py-3 border border-gray-300 dark:border-gray-600 rounded-lg shadow-sm focus:ring-2 focus:ring-primary focus:border-transparent bg-white dark:bg-gray-800 text-gray-900 dark:text-white transition-all duration-200"
textarea_classes = text_input_classes + " custom-scrollbar"
select_classes = text_input_classes  # <-- O nosso novo campo vai usar este estilo
checkbox_classes = "rounded border-gray-300 text-primary focus:ring-primary"
file_input_classes = "w-full text-base px-4 py-3 border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-lg shadow-sm focus:ring-2 focus:ring-primary focus:border-transparent bg-white dark:bg-gray-800 text-gray-900 dark:text-white transition-all duration-200"

# ============================================================================
# FORMULÁRIOS FINAIS E CORRIGIDOS
# ============================================================================


class QuestaoForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("texto_base", "enunciado", "proposicao_dois", "justificativa", "imagem"):
            self.fields[name].widget.attrs["aria-label"] = self.fields[name].label

    def clean(self):
        data = super().clean()
        if not data.get("texto_base"):
            self.add_error("texto_base", "Preencha o texto-base da questão.")
        if data.get("tipo_questao") == "ASSERCAO_RAZAO" and not data.get("proposicao_dois"):
            self.add_error("proposicao_dois", "Preencha a segunda proposição.")
        return data

    def clean_imagem(self):
        image = self.cleaned_data.get("imagem")
        if image and image.size > 5 * 1024 * 1024:
            raise forms.ValidationError("A imagem deve ter no máximo 5 MB.")
        return image

    class Meta:
        model = Questao

        # --- ALTERAÇÃO 1: Adicionar 'uso_prova' à lista de campos ---
        fields = [
            "professor_nome",
            "professor_email",
            "tipo_questao",
            "componente",
            "uso_prova",  # <-- ADICIONADO AQUI
            "texto_base",
            "imagem",
            "enunciado",
            "proposicao_dois",
            "justificativa",
        ]

        widgets = {
            "professor_nome": forms.TextInput(
                attrs={"class": text_input_classes, "placeholder": "Seu nome completo"}
            ),
            "professor_email": forms.EmailInput(
                attrs={"class": text_input_classes, "placeholder": "Seu melhor e-mail para contato"}
            ),
            "tipo_questao": forms.RadioSelect(),
            "componente": forms.Select(attrs={"class": select_classes}),
            # --- ALTERAÇÃO 2: Adicionar o widget para 'uso_prova' ---
            "uso_prova": forms.Select(attrs={"class": select_classes}),
            # --- FIM DA ALTERAÇÃO ---
            "texto_base": forms.Textarea(
                attrs={
                    "class": textarea_classes,
                    "rows": 6,
                    "placeholder": "Digite o texto-base da questão...",
                }
            ),
            "imagem": forms.FileInput(attrs={"class": file_input_classes}),
            "enunciado": forms.Textarea(
                attrs={
                    "class": textarea_classes,
                    "rows": 6,
                    "placeholder": "Digite o enunciado ou a Proposição I...",
                }
            ),
            "proposicao_dois": forms.Textarea(
                attrs={"class": textarea_classes, "rows": 4, "placeholder": "Digite a Proposição II..."}
            ),
            "justificativa": forms.Textarea(
                attrs={
                    "class": textarea_classes,
                    "rows": 6,
                    "placeholder": "Digite a justificativa da questão...",
                }
            ),
        }
        labels = {
            # Mantém os labels vazios como no teu original, se preferires
            "tipo_questao": "",
            "componente": "Componente curricular",
            "texto_base": "Texto-base",
            "imagem": "Imagem",
            "enunciado": "Enunciado",
            "proposicao_dois": "Proposição II",
            "justificativa": "Justificativa",
            "uso_prova": "Destino da questão",  # Adicionado label vazio
        }


class AlternativaForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        number = (
            int(self.prefix.rsplit("-", 1)[-1]) + 1
            if self.prefix and self.prefix.rsplit("-", 1)[-1].isdigit()
            else 1
        )
        self.fields["texto"].widget.attrs["aria-label"] = f"Texto da alternativa ou afirmativa {number}"
        self.fields["eh_correta"].widget.attrs["aria-label"] = f"Alternativa ou afirmativa {number} correta"

    """
    Este formulário para uma única alternativa está perfeito e não precisa de mudanças.
    """

    class Meta:
        model = Alternativa
        fields = ["texto", "eh_correta"]
        widgets = {
            "texto": forms.TextInput(
                attrs={"class": text_input_classes, "placeholder": "Digite o texto da alternativa..."}
            ),
            "eh_correta": forms.CheckboxInput(attrs={"class": checkbox_classes}),
        }
