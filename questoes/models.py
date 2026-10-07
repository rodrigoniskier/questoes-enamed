# questoes/models.py

from io import BytesIO

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import models
from PIL import Image

# ==========================================
# NOVA HIERARQUIA: SEMESTRE -> PERÍODO
# ==========================================


class Semestre(models.Model):
    nome = models.CharField(max_length=20, unique=True, help_text="Ex: 2025.1, 2025.2")
    ativo = models.BooleanField(default=True, help_text="Marque como ativo para o semestre corrente.")

    class Meta:
        verbose_name = "Semestre"
        verbose_name_plural = "Semestres"
        ordering = ["-nome"]

    def __str__(self):
        return self.nome


class Periodo(models.Model):
    nome = models.CharField(max_length=100, unique=True, help_text="Ex: 1º Período")
    # Novo campo para vincular ao Semestre
    semestre = models.ForeignKey(
        Semestre, on_delete=models.CASCADE, related_name="periodos", null=True, blank=True
    )

    def __str__(self):
        if self.semestre:
            return f"{self.nome} ({self.semestre.nome})"
        return self.nome


class ComponenteCurricular(models.Model):
    nome = models.CharField(max_length=200, help_text="Ex: Anatomia Humana")
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="componentes")
    numero_questoes_prova = models.PositiveIntegerField(
        default=5, help_text="Número de questões a serem selecionadas para a prova."
    )

    def __str__(self):
        return f"{self.nome} ({self.periodo.nome})"


# ==========================================
# MODELO PRINCIPAL DE QUESTÃO
# ==========================================


class Questao(models.Model):
    STATUS_CHOICES = [
        ("PENDENTE", "Pendente de Validação"),
        ("APROVADA", "Aprovada"),
        ("REPROVADA", "Reprovada"),
    ]

    TIPO_QUESTAO_CHOICES = [
        ("RESPOSTA_UNICA", "Resposta Única"),
        ("MULTIPLA_ESCOLHA", "Resposta Múltipla"),
        ("ASSERCAO_RAZAO", "Asserção-Razão"),
    ]

    USO_PROVA_CHOICES = [
        ("INTEGRADA", "Apenas Prova Integrada"),
        ("SIMULADO", "Apenas Simulado"),
        ("REPOSICAO", "Apenas Reposição"),
        ("RESIDENCIA", "Apenas Residência"),
        ("AMBAS", "Integrada e Reposição"),
    ]

    # Identificação e Classificação
    professor_nome = models.CharField(max_length=200)
    professor_email = models.EmailField()
    componente = models.ForeignKey(ComponenteCurricular, on_delete=models.CASCADE, related_name="questoes")
    tipo_questao = models.CharField(max_length=20, choices=TIPO_QUESTAO_CHOICES, default="RESPOSTA_UNICA")
    uso_prova = models.CharField(max_length=20, choices=USO_PROVA_CHOICES, default="INTEGRADA")

    # Conteúdo
    texto_base = models.TextField(blank=True, null=True)
    imagem = models.ImageField(upload_to="imagens_questoes/", blank=True, null=True)
    enunciado = models.TextField()
    proposicao_dois = models.TextField(
        blank=True, null=True, help_text="Usado apenas para questões de Asserção-Razão."
    )

    # Gabarito e Validação
    justificativa = models.TextField(help_text="Justificativa da resposta correta e dos distratores.")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDENTE",
        help_text="Questões aprovadas serão usadas nas provas. Reprovadas geram notificações de status.",
    )
    comentario_validacao = models.TextField(
        blank=True, null=True, help_text="Comentário do administrador ao aprovar ou reprovar a questão."
    )

    def __str__(self):
        return (self.enunciado[:50] + "...") if self.enunciado else "(Questão sem enunciado)"

    # Lógica de compressão mantida do arquivo original
    def save(self, *args, **kwargs):
        if self.imagem and not self.imagem._committed and not getattr(self, "_imagem_comprimida", False):
            try:
                img = Image.open(self.imagem)

                if img.mode != "RGB":
                    img = img.convert("RGB")

                max_width = 800
                if img.width > max_width:
                    ratio = max_width / float(img.width)
                    new_height = int(float(img.height) * float(ratio))
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

                output = BytesIO()
                img.save(output, format="JPEG", quality=70)
                output.seek(0)

                nome_original = self.imagem.name.split(".")[0]
                novo_nome = f"{nome_original}_comprimida.jpg"

                self.imagem = InMemoryUploadedFile(
                    output, "ImageField", novo_nome, "image/jpeg", output.getbuffer().nbytes, None
                )

                self._imagem_comprimida = True

            except Exception as e:
                print(f"Erro ao comprimir imagem da questão ID {self.id}: {e}")

        super().save(*args, **kwargs)


# ==========================================
# ALTERNATIVAS E CONFIGURAÇÕES DE IA
# ==========================================


class Alternativa(models.Model):
    questao = models.ForeignKey(Questao, on_delete=models.CASCADE, related_name="alternativas")
    texto = models.CharField(max_length=500, help_text="Texto da alternativa.")
    eh_correta = models.BooleanField(default=False, help_text="Marque se for a correta.")

    def __str__(self):
        return self.texto[:50]


class AIPrompt(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    texto_prompt = models.TextField()
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome


class SubmissionReceipt(models.Model):
    """Durable idempotency key; inserted and completed in one transaction."""

    token = models.UUIDField(primary_key=True, editable=False)
    questao = models.OneToOneField(
        Questao, on_delete=models.CASCADE, null=True, related_name="submission_receipt"
    )
    created_at = models.DateTimeField(auto_now_add=True)
