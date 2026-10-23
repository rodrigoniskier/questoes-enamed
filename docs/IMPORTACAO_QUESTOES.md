# Importação de questões entre componentes

O menu próprio do Django Admin abre `/admin/questoes/importacaoquestao/importar/`.
Exige usuário administrativo ativo e as permissões `questoes.importar_questoes`,
`questoes.view_questao` e `questoes.add_questao`. O superusuário já as possui.
Os recibos ficam disponíveis em **Registros de importação**, somente para leitura.

A interface usa o tema Jazzmin do Admin: cabeçalho e breadcrumbs, cartões,
campos e botões Bootstrap, tabela responsiva e avisos do próprio tema. Os ajustes
locais de largura ficam no template, sem novo arquivo estático. A correção visual
pode ser publicada com pull e Reload quando dependências, migração e estáticos da
importação já estiverem instalados; não exige outro collectstatic.

Escolha um semestre ativo e um componente doador canônico de período ativo.
Carregue as questões, marque individualmente ou use **Selecionar todas do filtro**,
escolha outro componente receptor e confirme. A seleção permanece entre páginas
e filtros; o limite é de 500 questões por operação, com 25 por página. O estado
assinado expira em uma hora e pertence à sessão. Ao trocar semestre ou doador,
carregue novamente os componentes e comece outra seleção.

Os bancos compartilhados existentes continuam funcionando: questões dos cadastros
consolidados no doador também aparecem quando pertencem ao mesmo semestre.
Cadastros consolidados não podem ser escolhidos diretamente como doador/receptor.
Semestres ou períodos inativos e componentes de outro semestre são rejeitados no
formulário e novamente no serviço, dentro da transação.

As cópias preservam autoria original, tipo, uso da prova, textos, proposições,
justificativa e alternativas com o gabarito e a ordem por chave primária usada
pela aplicação. Imagens são copiadas para arquivos distintos, sem recompressão,
para que `django-cleanup` não remova a imagem original ao excluir a cópia.
Toda cópia começa `PENDENTE`, sem aprovação, comentário de validação ou marcação
de notificação herdados. O comentário original permanece no recibo de importação.
Não há campo próprio de assunto ou data de cadastro no modelo original; a tabela
mostra os campos reais disponíveis, incluindo autor e tipo.

Recibos de submissão, provas, notificações e históricos de pré-auditoria não são
duplicados. A migração aditiva `0015_importacaoquestao` cria apenas o recibo,
reutilizando `Semestre`, `Periodo` e os demais modelos existentes. O recibo mantém
o identificador da origem mesmo se ela for excluída; sua restrição única por
origem/receptor também permanece se a cópia for excluída. Assim, reenvios e novas
tentativas para a mesma combinação são ignorados, sem deduzir equivalência por
enunciado. Importações antigas sem recibo comprovado não ganham rastreabilidade
retroativa. O responsável pela importação é registrado sem substituir o autor.

O serviço usa transação durável, serialização por semestre e bloqueio dos registros
relacionados. SQLite adquire o bloqueio de escrita antes de consultar duplicatas;
a restrição única permanece como garantia no banco. Falhas revertem as gravações
e removem somente os arquivos novos. Erros transitórios preservam a confirmação
para nova tentativa. A seleção é validada no servidor e protegida por CSRF, pelas
permissões do Admin e pela assinatura específica da sessão.

## Verificação isolada

```bash
python manage.py test --settings=gerador_provas.test_settings
python -m unittest ops.test_deploy_importacao
python manage.py makemigrations --check --dry-run --settings=gerador_provas.test_settings
python -m pip_audit -r requirements.txt
```

O teste opcional de navegador usa Playwright 1.64.1 e Chromium, dados artificiais,
SMTP/IA simulados e banco temporário. Execute com `RUN_BROWSER_TESTS=1` após instalar
o Chromium do Playwright. `BROWSER_EXECUTABLE` permite escolher um Chromium local;
`BROWSER_SCREENSHOT` salva as capturas desktop e móvel. O CI executa a suíte Django,
os testes operacionais e um job separado para o navegador.

## Deploy e recuperação

O nome do diretório de produção é histórico: `/home/rodrigoniskier/prova-naped`
aponta para **rodrigoniskier/questoes-enamed**. Confirme sempre o remote e o SHA,
sem presumir que o diretório identifica o repositório.

Execute `ops/deploy_importacao.py` com o Python do virtualenv efetivamente configurado
no painel. Ele precisa estar disponível fora do checkout antes do pull; obtenha-o
de uma revisão imutável aprovada e verifique seu SHA-256 antes de executar.

```bash
/home/rodrigoniskier/.virtualenvs/myenv/bin/python /home/rodrigoniskier/deploy_importacao.py \
  --checkout /home/rodrigoniskier/prova-naped \
  --expected-before SHA_ANTERIOR_CONFIRMADO \
  --approved-sha SHA_APROVADO_NA_MAIN \
  --wsgi /var/www/rodrigoniskier_pythonanywhere_com_wsgi.py \
  --backup-dir /home/rodrigoniskier/.private_backups/importacao
```

O procedimento interrompe diante de checkout sujo, revisão divergente, migrações
anteriores pendentes ou falha de backup. Antes de alterar código/dependências, usa
o backup nativo SQLite, valida integridade e referências, arquiva mídias, preserva
`.env`, WSGI, pacote Git e inventário de dependências em diretório privado. Preserva
também o ledger de IA existente. Não publica esses arquivos nem seus conteúdos.

Depois executa fetch/pull com avanço direto para o SHA aprovado, instala os pins
somente se `requirements.txt` mudou, verifica dependências/Django, aplica migrações,
coleta estáticos e compara os registros acadêmicos e históricos existentes com o
backup, além dos hashes das imagens. Atualiza somente `RELEASE_REVISION` em `.env`.
Um recibo privado registra cada etapa. A saída final significa **reload pendente**.

Recarregue exclusivamente `rodrigoniskier.pythonanywhere.com` pela interface oficial,
ou pela API oficial quando já houver autenticação disponível. Confirme a revisão
em `/app/api/version/`, verifique os logs e navegue no Admin autenticado, incluindo
semestre/doador/receptor, filtros e seleção, sem concluir importações com questões
reais. A importação completa é exercitada nos dados artificiais dos testes locais/CI.

Se alguma etapa falhar, pare e use o recibo e o log privado para localizar a falha.
Não há reset, limpeza ou restauração automática: restaurar um backup por cima de
gravações posteriores poderia perder trabalho legítimo. A migração é aditiva;
um rollback do código deve preservar a nova tabela e seus recibos. Antes de
restaurar banco/mídia, faça outro backup, avalie gravações posteriores e coordene
uma janela de manutenção. Não remova backups sem autorização específica.
