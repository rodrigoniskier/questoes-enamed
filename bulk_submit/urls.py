# Em: bulk_submit/urls.py

from django.urls import path

from . import views
from .views import upload_grade_csv_view  # Adicione a nova view aqui

urlpatterns = [
    # Define a URL para acessar a view do formulário de submissão em massa
    path("", views.bulk_submit_view, name="bulk_submit_form"),
    # Você pode adicionar outras URLs específicas para este app aqui no futuro, se necessário
    path("configurar-grade/", upload_grade_csv_view, name="upload_grade_csv"),  # Nova rota!
]
