from django.contrib import admin

from .models import (
    AlteracaoPreAuditoria,
    DecisaoPreAuditoria,
    EvidenciaRevisor,
    LotePreAuditoria,
    ParecerPreAuditoria,
    SolicitacaoCorrecao,
)


class ImmutableAuditAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ParecerPreAuditoria)
class ParecerPreAuditoriaAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("questao", "resultado", "criado_em", "revisor")
    list_filter = ("resultado", "criado_em")
    search_fields = ("questao__enunciado", "questao__professor_nome", "questao__professor_email")
    readonly_fields = (
        "questao",
        "resultado",
        "fingerprint",
        "achados",
        "indicadores",
        "criado_em",
        "revisor",
    )
    ordering = ("-criado_em",)


@admin.register(AlteracaoPreAuditoria)
class AlteracaoPreAuditoriaAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("questao", "revisor", "criada_em")
    search_fields = ("questao__enunciado", "revisor__username")
    readonly_fields = ("questao", "revisor", "antes", "depois", "criada_em")
    ordering = ("-criada_em",)


@admin.register(EvidenciaRevisor)
class EvidenciaRevisorAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("parecer", "criterio_id", "revisor", "criada_em")
    list_filter = ("criterio_id", "criada_em")
    readonly_fields = ("parecer", "criterio_id", "evidencia", "referencia", "revisor", "criada_em")


@admin.register(DecisaoPreAuditoria)
class DecisaoPreAuditoriaAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("questao", "acao", "revisor", "criada_em")
    list_filter = ("acao", "criada_em")
    readonly_fields = (
        "questao",
        "parecer",
        "revisor",
        "acao",
        "fingerprint",
        "justificativa",
        "criada_em",
    )


@admin.register(SolicitacaoCorrecao)
class SolicitacaoCorrecaoAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("questao", "revisor", "criada_em", "expira_em", "utilizada_em", "email_enviado_em")
    list_filter = ("criada_em", "expira_em", "utilizada_em")
    readonly_fields = (
        "questao",
        "parecer",
        "revisor",
        "nonce",
        "mensagem",
        "criada_em",
        "expira_em",
        "utilizada_em",
        "email_enviado_em",
    )


@admin.register(LotePreAuditoria)
class LotePreAuditoriaAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("id", "status", "posicao", "revisor_nome", "criado_em", "atualizado_em")
    list_filter = ("status", "criado_em")
    readonly_fields = (
        "id",
        "revisor",
        "revisor_nome",
        "questao_ids",
        "posicao",
        "status",
        "erro",
        "claim_token",
        "iniciado_em",
        "criado_em",
        "atualizado_em",
    )
