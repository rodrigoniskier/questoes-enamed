import uuid

from django.conf import settings
from django.db import models

from questoes.models import Questao


class ParecerPreAuditoria(models.Model):
    class Resultado(models.TextChoices):
        PENDENCIAS = "PENDENCIAS", "Pendências"
        INCONCLUSIVA = "INCONCLUSIVA", "Verificação inconclusiva"
        APROVADA = "APROVADA", "Aprovada na pré-auditoria"

    questao = models.ForeignKey(Questao, on_delete=models.PROTECT, related_name="pareceres_pre_auditoria")
    resultado = models.CharField(max_length=16, choices=Resultado.choices)
    request_id = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    fingerprint = models.CharField(max_length=64)
    achados = models.JSONField(default=list)
    indicadores = models.JSONField(default=dict)
    criado_em = models.DateTimeField(auto_now_add=True)
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pareceres_pre_auditoria",
    )
    revisor_nome = models.CharField(max_length=150, blank=True, default="")
    arquivado_em = models.DateTimeField(null=True, blank=True)
    arquivado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pareceres_pre_auditoria_arquivados",
    )

    class Meta:
        ordering = ["-criado_em", "-pk"]
        permissions = [("review_parecerpreauditoria", "Pode revisar e confirmar pré-auditorias")]
        indexes = [models.Index(fields=["questao", "-criado_em"])]

    def __str__(self):
        return f"Pré-auditoria da questão {self.questao_id}: {self.get_resultado_display()}"


class AlteracaoPreAuditoria(models.Model):
    class Origem(models.TextChoices):
        REVISOR = "REVISOR", "Revisor"
        PROFESSOR = "PROFESSOR", "Professor"

    questao = models.ForeignKey(Questao, on_delete=models.PROTECT, related_name="alteracoes_pre_auditoria")
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="alteracoes_pre_auditoria",
    )
    revisor_nome = models.CharField(max_length=150, blank=True, default="")
    antes = models.JSONField()
    depois = models.JSONField()
    origem = models.CharField(max_length=12, choices=Origem.choices, default=Origem.REVISOR)
    solicitacao_correcao = models.ForeignKey(
        "SolicitacaoCorrecao",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="alteracoes",
    )
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criada_em", "-pk"]

    def __str__(self):
        return f"Edição da questão {self.questao_id} por {self.revisor_id or 'usuário removido'}"


class EvidenciaRevisor(models.Model):
    parecer = models.ForeignKey(ParecerPreAuditoria, on_delete=models.PROTECT, related_name="evidencias")
    criterio_id = models.CharField(max_length=12)
    evidencia = models.TextField()
    referencia = models.TextField(blank=True, default="")
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="evidencias_pre_auditoria",
    )
    revisor_nome = models.CharField(max_length=150, blank=True, default="")
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criterio_id", "-criada_em"]


class DecisaoPreAuditoria(models.Model):
    class Acao(models.TextChoices):
        APROVADA = "APROVADA", "Aprovação confirmada"
        CORRECAO = "CORRECAO", "Correção solicitada"

    questao = models.ForeignKey(Questao, on_delete=models.PROTECT, related_name="decisoes_pre_auditoria")
    parecer = models.ForeignKey(ParecerPreAuditoria, on_delete=models.PROTECT, related_name="decisoes")
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="decisoes_pre_auditoria",
    )
    revisor_nome = models.CharField(max_length=150, blank=True, default="")
    acao = models.CharField(max_length=12, choices=Acao.choices)
    fingerprint = models.CharField(max_length=64)
    justificativa = models.TextField(blank=True)
    aprovacao_excepcional = models.BooleanField(default=False)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criada_em", "-pk"]


class SolicitacaoCorrecao(models.Model):
    questao = models.ForeignKey(Questao, on_delete=models.PROTECT, related_name="solicitacoes_correcao")
    parecer = models.ForeignKey(
        ParecerPreAuditoria, on_delete=models.PROTECT, related_name="solicitacoes_correcao"
    )
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="solicitacoes_correcao_pre_auditoria",
    )
    revisor_nome = models.CharField(max_length=150, blank=True, default="")
    nonce = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    mensagem = models.TextField()
    criada_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    utilizada_em = models.DateTimeField(null=True, blank=True)
    email_enviado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criada_em", "-pk"]


class LotePreAuditoria(models.Model):
    class Status(models.TextChoices):
        AGUARDANDO = "AGUARDANDO", "Aguardando"
        PROCESSANDO = "PROCESSANDO", "Processando uma questão"
        CONCLUIDO = "CONCLUIDO", "Concluído"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="lotes_pre_auditoria",
    )
    revisor_nome = models.CharField(max_length=150, blank=True, default="")
    questao_ids = models.JSONField(default=list)
    posicao = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AGUARDANDO)
    erro = models.CharField(max_length=300, blank=True, default="")
    claim_token = models.UUIDField(null=True, blank=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]


class OperacaoLimpezaPreAuditoria(models.Model):
    revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="limpezas_pre_auditoria",
    )
    revisor_nome = models.CharField(max_length=150)
    questao_ids = models.JSONField(default=list)
    pareceres_solicitados = models.JSONField(default=list)
    pareceres_excluidos = models.JSONField(default=list)
    pareceres_arquivados = models.JSONField(default=list)
    nao_elegiveis = models.PositiveIntegerField(default=0)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criada_em", "-pk"]
