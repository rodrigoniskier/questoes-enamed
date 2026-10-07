# questoes/urls.py

from django.urls import path

from . import views

urlpatterns = [
    # --- NOVAS URLS PARA O FLUXO DE SUBMISSÃO ---
    path("submeter/", views.submeter_selecao_view, name="submeter_selecao"),
    path(
        "submeter/resposta-unica/",
        views.submeter_form_view,
        {"tipo_questao": "RESPOSTA_UNICA"},
        name="submeter_resposta_unica",
    ),
    path(
        "submeter/resposta-multipla/",
        views.submeter_form_view,
        {"tipo_questao": "MULTIPLA_ESCOLHA"},
        name="submeter_resposta_multipla",
    ),
    path(
        "submeter/assercao-razao/",
        views.submeter_form_view,
        {"tipo_questao": "ASSERCAO_RAZAO"},
        name="submeter_assercao_razao",
    ),
    # --- APIs E OUTRAS URLS ---
    path("api/get-componentes/", views.get_componentes_por_periodo, name="api_get_componentes"),
    path("api/get-historico/", views.get_historico_questoes, name="api_get_historico"),
    path("api/gerar-questao/", views.api_gerar_questao_view, name="api_gerar_questao"),
    path("api/csrf/", views.api_csrf_view, name="api_csrf"),
    # --- URLS DE PROVA ---
    path("gerar-prova/", views.pagina_gerar_prova, name="pagina_gerar_prova"),
    path("prova-gerada/", views.prova_gerada_view, name="prova_gerada"),
    path("prova-gabarito/", views.prova_gabarito_view, name="prova_gabarito"),
    # --- ALTERAÇÃO AQUI: NOVA URL DO MONTADOR MANUAL ---
    path("montador-manual/", views.montador_manual_view, name="montador_manual"),
    # --- FIM DA ALTERAÇÃO ---
    path("submeter/visualizar/", views.visualizar_questoes_view, name="submeter_visualizar_questoes"),
]
