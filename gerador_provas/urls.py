# gerador_provas/urls.py

from django.contrib import admin
from django.urls import path, include # Importa 'include'
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # URL do admin
    path('admin/', admin.site.urls),

    # URLs da app 'questoes' (existente)
    path('app/', include('questoes.urls')),

    # --- NOVA LINHA ADICIONADA ---
    # URLs da nova app 'bulk_submit'
    path('bulk/', include('bulk_submit.urls')),
    # --- FIM DA NOVA LINHA ---

    # Você pode adicionar outras URLs de nível de projeto aqui, se houver.
    # Exemplo: path('', include('outra_app.urls')), # Para a página inicial
]

# Configuração para servir arquivos de mídia durante o desenvolvimento (mantida)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Opcional: Configuração para servir arquivos estáticos (geralmente não necessário se DEBUG=True)
# if settings.DEBUG:
#     urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)