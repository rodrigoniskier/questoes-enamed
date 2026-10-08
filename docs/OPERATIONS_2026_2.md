# Operação e recuperação — 2026.2

## Antes de publicar

Identifique o diretório real, virtualenv, WSGI e `DJANGO_DATABASE_PATH`. Confira a revisão remota e alterações locais de código. Faça backup privado e consistente com `sqlite3.Connection.backup`; verifique `PRAGMA integrity_check`; arquive mídias, `.env`, WSGI e um `git bundle --all`. Registre revisão anterior e contagens. Nunca inclua esses arquivos em commits, estáticos, mídia pública ou relatórios públicos.

## Atualização que preserva um SQLite anteriormente rastreado

Na primeira atualização que remove o banco do Git, **não faça um pull que apague o arquivo de produção**. Após backup e confirmação de que não há alterações locais de código, atualize os arquivos rastreados excetuando dados; atualize apenas o índice/referência local, sem `reset --hard`:

```sh
git fetch origin main
git restore --source=origin/main --worktree -- . ':!db.sqlite3' ':!relatorio_auditoria_admin.csv'
git reset --mixed origin/main
python -m pip install -r requirements.txt
python manage.py check
python manage.py migrate --noinput
python manage.py configurar_semestre_2026_2 --dry-run
python manage.py configurar_semestre_2026_2
python manage.py configurar_semestre_2026_2
python manage.py collectstatic --noinput
```

O banco e o CSV permanecem no disco e passam a ser ignorados. Não aplique esta receita quando houver alterações locais de código sem antes conciliá-las. Em atualizações posteriores com código limpo, use `git pull --ff-only`. Confirme que `db.sqlite3` está ignorado e não rastreado. As migrações acadêmicas permanecem até 0014; o controle da IA usa um SQLite privado separado.

Defina `RELEASE_REVISION` no `.env` para a revisão aplicada e recarregue na aba Web. `GET /app/api/version/` deve retornar `version=2026.2` e a revisão correta; confira também páginas e estáticos. A mera leitura de Git não prova qual versão o worker executa.

Compare o banco e as mídias sem restaurar dados antigos:

```sh
python ops/verify_integrity.py --baseline /caminho/privado/production.sqlite3 --current /caminho/real/db.sqlite3 --media-archive /caminho/privado/media.tar.gz --media-root /caminho/real/media
```

A comparação permite novos registros e exige igualdade de todos os campos de cada registro preexistente das tabelas acadêmicas, usuários e histórico administrativo. Não imprime dados pessoais. Se houver alteração pedagógica legítima concorrente, investigue a divergência; não reverta o banco automaticamente.

## IA

`google-genai` substitui o SDK legado em geração, avaliação e importação. O modelo é configurado por `GEMINI_MODEL` e deve ser verificado com a chave real usando `models.get` ou `models.list`; não deduza disponibilidade pelo nome. Uma geração sintética real supervisionada basta para verificar a operação. Não registre chaves, prompts pessoais ou corpos de erro do provedor.

O ledger padrão é `private/ai-control.sqlite3`, com permissões privadas; nunca o mapeie como estático ou mídia. Todos os workers deste host devem usar **o mesmo caminho**. `BEGIN IMMEDIATE` torna a admissão atômica entre processos. Múltiplos hosts exigiriam armazenamento coordenado apropriado; não copie ledgers independentes entre hosts.

Padrões conservadores: 6 tentativas globais por janela móvel de 60s, 100 por dia UTC, 2 por origem direta por 60s e 2 simultâneas. As avaliações e importações administrativas compartilham a mesma cota global e a origem administrativa. O IP direto é transformado em HMAC, sem armazenar endereço bruto; não confiamos em `X-Forwarded-For` fornecido pelo cliente. Confira o `REMOTE_ADDR` efetivamente fornecido pelo host: se ele representar um proxy compartilhado, a limitação por origem será conservadora e conjunta. A cota global continua válida.

Há timeout de 60s, limite de 4096 tokens de saída e uma única tentativa no SDK. Cotas limitam chamadas/saída, sem garantir um teto monetário absoluto: ajuste também as cotas e orçamento no projeto Google. HTTP 429 inclui `Retry-After`; controle indisponível bloqueia a geração com 503. Tentativas aceitas consomem cota mesmo em falhas ou timeout.

Um UUID identifica a geração; rascunhos concluídos podem ser recuperados com a mesma chave sem nova chamada. Pendências/falhas retornam 409 sem repetição automática. A retenção é de 48h, eliminada na próxima admissão; backups também precisam de retenção privada. Gerar explicitamente outro rascunho cria nova chave e pode consumir outra chamada. CSRF só pode provocar uma repetição quando a resposta JSON comprovar rejeição antes de chegar à IA.

O botão **Recuperar último rascunho** reutiliza o pedido anterior da aba, incluindo seu tema e parâmetros. Ele é habilitado após uma tentativa e exige confirmação antes de substituir conteúdo preenchido. O pedido fica apenas na memória da aba; recarregar/fechar a página encerra essa recuperação pela interface. O botão **Gerar Rascunho parametrizado** cria um novo pedido explícito. Resposta múltipla oferece cinco campos e aceita 3–5 afirmativas; abas antigas com menos campos rejeitam um rascunho que exceda sua capacidade, sem descartar afirmativas.

## Recuperação do professor

O formulário envia `FormData` sem abandonar a página. Falhas de rede, validação, banco, HTML inesperado ou tempo limite preservam textos, gabarito, checklists e o arquivo selecionado. A chave de submissão permanece para repetir manualmente sem duplicar questões já aceitas. Uma chave expirada recebe nova autorização após a rejeição; a próxima tentativa continua explícita. Sem JavaScript, a submissão HTML continua disponível; por restrição do navegador, imagens precisam ser selecionadas novamente após erro que recarrega a página.

Importações administrativas também usam recibo durável e transação para o lote, validação integral antes de gravar, limite de 100 itens, texto de 20 mil caracteres e arquivo de 5 MB. Backups mantêm alternativas históricas válidas (2–5), sem forçar a regra atual sobre seus textos. Extração por IA precisa cumprir o contrato pedagógico; gabaritos não podem ser inventados por padrão. Uma repetição aceita não reimporta o lote. O formulário de importação em massa mantém o fluxo HTML e requer nova seleção do arquivo após falhas.

## Rollback

Reverta apenas código à revisão previamente registrada, usando a mesma exclusão de banco e CSV na restauração. Não use `reset --hard`, `git clean`, `migrate zero` nem restaure um SQLite antigo por cima dos envios recentes. Preserve schema aditivo, recibos e o ledger da IA; reinstale dependências da revisão, colete estáticos, atualize `RELEASE_REVISION` e recarregue. Confirme integridade e funcionamento. Antes de qualquer reparo de dados, obtenha outro backup do estado atual e concilie novos registros.
