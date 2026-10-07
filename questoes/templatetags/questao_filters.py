# Em: prova-naped/questoes/templatetags/questao_filters.py

import re  # Vamos usar Expressões Regulares (RegEx)

from django import template

register = template.Library()

# 1. Definimos o padrão de RegEx que queremos "limpar".
# Este padrão procura por:
#   ^        -> no início do texto
#   \s* -> espaços em branco opcionais
#   ( ... )  -> um grupo de prefixos possíveis:
#     (V|IV|I{1,3}|v|iv|i{1,3}) -> Romanos I, II, III, IV, V (maiúsculos ou minúsculos)
#     |        -> OU
#     ([A-Ea-e]) -> Letras A, B, C, D, E (maiúsculas ou minúsculas)
#   \s* -> espaços em branco opcionais
#   (\.|\))  -> um ponto literal (.) OU um parêntese literal ())
#   \s* -> espaços em branco opcionais
#
PREFIX_REGEX = re.compile(r"^\s*(((V|IV|I{1,3}|v|iv|i{1,3})|([A-Ea-e]))\s*(\.|\))\s*)")


@register.filter(name="limpar_prefixo")
def limpar_prefixo(texto_da_alternativa):
    """
    Remove um prefixo de alternativa (ex: 'A)', 'I.', 'c)', 'iii.')
    do início do texto da alternativa.
    """

    # Se o texto estiver vazio, retorna vazio
    if not texto_da_alternativa:
        return ""

    # A função re.sub() substitui o padrão (PREFIX_REGEX)
    # por uma string vazia ("") no texto original.
    texto_limpo = PREFIX_REGEX.sub("", texto_da_alternativa)

    return texto_limpo
