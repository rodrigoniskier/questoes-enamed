# Capa editorial UNIPÊ — validação em 02/11/2026

A capa usa exclusivamente HTML/CSS e a imagem oficial já existente em
`static/images/logo.jpg`. Azul-marinho `#062C55`, branco e um filete dourado
`#F7C21B` compõem a identidade; títulos, campos e disciplinas são texto real.
Os estilos anteriores da capa e seus SVGs foram removidos integralmente.

O cabeçalho centralizado antecede a identificação da avaliação, a modalidade,
“Boa Prova!” e os campos para escrita manual. A lista usa duas colunas, fonte
10 pt e quadrados de marcação. Nomes completos quebram naturalmente. Não há
posição absoluta, altura máxima, recorte, redução automática ou dependência
externa na capa.

## Impressão e continuação

A4 vertical. A capa tem margens de 16 mm no topo, 18 mm nas laterais e 14 mm na
base. A página nomeada mantém essas margens em todas as continuações. Linhas
da tabela não se dividem entre páginas; seu cabeçalho se repete nas folhas
seguintes. O rodapé encerra a lista e apresenta o mesmo `codigo_prova` usado
pelo cartão e pelo CSV. O miolo e o cartão conservam seus estilos e sua lógica.

Cores, linhas, texto e logo não dependem de imprimir gráficos de fundo.
A visualização em escala de cinza conserva a hierarquia tipográfica.

## Verificações executadas

Base inspecionada: `7badebb82e573288ed0aec22c679057396825ec9`.
Dados exclusivamente sintéticos e banco isolado de testes.

- 263 testes Django aprovados, incluindo os testes de navegador habilitados.
- 7 testes das ferramentas de implantação aprovados.
- Ruff (lint e formatação), verificação de migrações e `git diff --check` aprovados.
- Auditoria das dependências de produção sem vulnerabilidades conhecidas.
- Conferência da igualdade do código entre a capa, o cartão e o CSV da seleção
  efetivamente gerada, incluindo questões com quatro e cinco alternativas.
- Reposição, residência, simulado e multiperíodo preservados e cobertos por testes.
- Inspeção visual das capturas e das páginas PDF rasterizadas, além das
  verificações automáticas de geometria e paginação.

| Cenário | Resultado do PDF da capa sem fundos |
| --- | --- |
| 1 componente | 1 página A4 |
| 8 componentes | 1 página A4 |
| 12 componentes | 1 página A4 |
| 24 componentes | 1 página A4 |
| 24 componentes com nomes extensos e modalidade longa | 2 páginas A4 |
| 80 componentes, incluindo nomes extensos | 3 páginas A4 |
| Prova completa básica | 3 páginas: capa, questões e cartão |

Foram exportadas também versões em preto e branco para 1, 8, 12 e 24
componentes. Uma conferência independente dos PDFs verificou dimensões A4,
texto selecionável, ausência de páginas vazias, nomes completos sem duplicação
ou omissão e texto dentro dos limites físicos das folhas. As continuações
repetem o cabeçalho da lista. O logo mantém o arquivo e suas proporções originais.

O teste de navegador salva PNGs e PDFs quando `BROWSER_SCREENSHOT` está
definido; a configuração de CI existente publica a pasta como artefato
`telas-regressao`. O diretório usado localmente foi `/tmp/unipe-capa-qa/`.

Para reproduzir a suíte completa, com Playwright/Chromium já disponíveis:

```bash
RUN_BROWSER_TESTS=1 BROWSER_SCREENSHOT=/tmp/unipe-capa-qa/tela.png \
  python manage.py test --settings=gerador_provas.test_settings
```

## Atualização no PythonAnywhere

Esta alteração não modifica dependências, banco, migrações ou arquivos estáticos.
O CSS permanece no template e reutiliza o logo já publicado. Portanto, para esta
alteração, não são necessários instalação de pacotes, migração ou collectstatic.

```bash
cd ~/prova-naped
git pull --ff-only origin main
```

Depois, usar **Reload** na aba **Web**. A implantação e a conferência na aplicação
de produção ficam para essa etapa manual; os testes acima são locais/CI.
