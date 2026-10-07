"""CSRF errors for the JSON API; keep Django's normal protection for HTML forms."""

from django.http import JsonResponse
from django.urls import reverse
from django.views.csrf import csrf_failure as default_csrf_failure


def csrf_failure(request, reason=""):
    if request.path == reverse("api_gerar_questao"):
        return JsonResponse(
            {
                "erro": "A autenticação de segurança expirou. Renove o token e tente novamente.",
                "codigo": "csrf_invalido",
            },
            status=403,
        )
    return default_csrf_failure(request, reason=reason)
