from django import forms
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet

from questoes.models import Alternativa, Questao
from questoes.submission import alternative_count, validate_alternatives


class QuestaoRevisaoForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["texto_base"].required = True

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("tipo_questao") == "ASSERCAO_RAZAO" and not cleaned.get("proposicao_dois"):
            self.add_error("proposicao_dois", "Preencha a proposição II.")
        return cleaned

    def clean_imagem(self):
        image = self.cleaned_data.get("imagem")
        if image and image.size > 5 * 1024 * 1024:
            raise forms.ValidationError("A imagem deve ter no máximo 5 MB.")
        return image

    class Meta:
        model = Questao
        fields = (
            "tipo_questao",
            "uso_prova",
            "texto_base",
            "imagem",
            "enunciado",
            "proposicao_dois",
            "justificativa",
            "comentario_validacao",
        )
        widgets = {
            "texto_base": forms.Textarea(attrs={"rows": 5}),
            "enunciado": forms.Textarea(attrs={"rows": 5}),
            "proposicao_dois": forms.Textarea(attrs={"rows": 4}),
            "justificativa": forms.Textarea(attrs={"rows": 6}),
            "comentario_validacao": forms.Textarea(attrs={"rows": 4}),
        }


class CorrecaoProfessorForm(QuestaoRevisaoForm):
    class Meta:
        model = Questao
        fields = (
            "tipo_questao",
            "uso_prova",
            "texto_base",
            "imagem",
            "enunciado",
            "proposicao_dois",
            "justificativa",
        )


class RevisaoAlternativasFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        style = self.instance.tipo_questao
        alternatives = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            text = form.cleaned_data.get("texto", "")
            correct = form.cleaned_data.get("eh_correta", False)
            if not text and not correct:
                continue
            alternatives.append({"texto": text, "eh_correta": correct})
        count = 5 if style == "ASSERCAO_RAZAO" else alternative_count(self.instance.componente, style)
        validation_style = "RESPOSTA_UNICA" if style == "ASSERCAO_RAZAO" else style
        try:
            validate_alternatives(alternatives, validation_style, count)
        except forms.ValidationError as error:
            raise forms.ValidationError(error.messages) from error


class EvidenciaRevisorForm(forms.Form):
    criterio_id = forms.CharField(max_length=12)
    evidencia = forms.CharField(max_length=2000, min_length=15, widget=forms.Textarea(attrs={"rows": 4}))
    referencia = forms.CharField(max_length=1000, required=False, widget=forms.TextInput())


class DecisaoForm(forms.Form):
    confirmar_aprovacao = forms.BooleanField(
        required=True,
        error_messages={"required": "Confirme explicitamente a aprovação da questão."},
    )
    justificativa = forms.CharField(max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 4}))


class PedidoCorrecaoForm(forms.Form):
    mensagem = forms.CharField(max_length=2000, min_length=20, widget=forms.Textarea(attrs={"rows": 5}))


AlternativasRevisaoFormSet = inlineformset_factory(
    Questao,
    Alternativa,
    fields=("texto", "eh_correta"),
    formset=RevisaoAlternativasFormSet,
    extra=1,
    can_delete=True,
    max_num=5,
    validate_max=True,
)
