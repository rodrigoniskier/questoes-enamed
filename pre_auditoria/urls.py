from django.urls import path

from . import views

app_name = "pre_auditoria"

urlpatterns = [
    path("questao/<int:questao_id>/parecer/", views.decidir, name="decidir"),
    path("", views.lista, name="lista"),
    path("questao/<int:questao_id>/", views.detalhe, name="detalhe"),
    path("questao/<int:questao_id>/editar/", views.editar, name="editar"),
    path("questao/<int:questao_id>/iniciar/", views.iniciar, name="iniciar"),
    path("questao/<int:questao_id>/aprovar/", views.aprovar, name="aprovar"),
    path("auditorias/excluir/", views.excluir_auditorias, name="excluir_auditorias"),
    path(
        "questao/<int:questao_id>/solicitar-correcao/",
        views.solicitar_correcao,
        name="solicitar_correcao",
    ),
    path("parecer/<int:parecer_id>/evidencia/", views.registrar_evidencia, name="registrar_evidencia"),
    path("aprovar-lote/", views.aprovar_lote, name="aprovar_lote"),
    path("correcao/<str:token>/", views.correcao_professor, name="correcao_professor"),
    path("lotes/criar/", views.criar_lote, name="criar_lote"),
    path("lotes/<uuid:lote_id>/", views.detalhe_lote, name="detalhe_lote"),
    path("lotes/<uuid:lote_id>/proximo/", views.processar_proximo, name="processar_proximo"),
]
