# Questões ENAMED / UNIPÊ — Blue Steel

O visual compartilha o tema Blue Steel do `rodrigoniskier/appsermao`, conforme seu `DESIGN.md` e `static/css/app.css` (blob de referência `9855a336395c3b3779a4b66017c560fadaef085e`).

Use azul metálico nos cabeçalhos e ações, fundo cinza aço `#e8eef3`, painéis brancos, texto `#142536`, bordas `#b7c4cf` e amarelo `#f2c300` nos destaques. A tipografia é Inter com fallback para a fonte do sistema. Painéis têm cantos de 6px, campos de 4px e botões de 5px. Evite pílulas grandes, roxo, transparência de vidro e gradientes por trás de textos longos.

O tema comum fica em `questoes/static/questoes/app.css`, carregado depois das utilidades locais. Os templates usam uma versão explícita no endereço do CSS para atualizar caches após a publicação. Navegação, seleção, formulários, consulta de questões, montagem e importação compartilham esse tema. Documentos de prova e gabarito mantêm a apresentação própria de impressão.

Preserve foco visível, contraste, mensagens textuais de estado e alvos de toque de pelo menos 40px. O amarelo é fundo ou marcador e recebe texto azul profundo. Em telas pequenas, a navegação e as ações se reorganizam em linhas e colunas. Não altere validação, campos, rotas ou submissão para mudar o tema.
