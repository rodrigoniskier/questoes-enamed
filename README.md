# Questões ENAMED + IA

Aplicação Django para elaborar questões, revisar itens e montar avaliações de Medicina.

O fluxo de submissão oferece **Enviar e criar outra no mesmo estilo**, **Enviar e selecionar outro estilo de questão** e **Enviar e concluir**. Os dois primeiros mantêm componente curricular, professor, e-mail e destino da prova na sessão do navegador. Conteúdo, alternativas, gabarito, imagem, comando de IA e checklists são reiniciados. A continuidade funciona sem colocar dados pessoais na URL e separa contextos de abas diferentes.

## Desenvolvimento

Python 3.10 ou superior. Crie um ambiente virtual, instale `requirements.txt` e configure `.env` a partir de `.env.example`. Para desenvolvimento, defina `DJANGO_DEBUG=true`, hosts locais e um caminho de banco de dados separado em `DJANGO_DATABASE_PATH`. Não use o SQLite versionado como banco de testes.

```sh
python manage.py migrate
python manage.py runserver
```

O CSS compilado está versionado: Node não é necessário no PythonAnywhere. Para alterar classes/utilitários, instale as dependências de desenvolvimento com `pnpm install --frozen-lockfile` e execute `pnpm run build:css`.

## Validação

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check questoes bulk_submit gerador_provas
python -m ruff format --check questoes bulk_submit gerador_provas
python manage.py test --settings=gerador_provas.test_settings
python manage.py makemigrations --check --dry-run --settings=gerador_provas.test_settings
python -m pip_audit -r requirements.txt
```

Testes usam banco em memória, e-mail simulado e IA simulada. Nenhuma geração paga ou notificação real é necessária para validar o fluxo.

## Implantação no PythonAnywhere

Antes do pull, faça backup consistente do SQLite usando `sqlite3.Connection.backup`, guarde o commit atual e confirme que os arquivos de código estão limpos. O banco existente contém dados de produção e não deve ser restaurado a partir do Git. Preserve `.env`, mídias e configuração WSGI.

```sh
git pull --ff-only origin main
python -m pip install -r requirements.txt
python manage.py check
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Recarregue na aba Web, confirme as páginas e compare os registros existentes antes/depois. A migração 0011 cria recibos de envio; a 0012 mantém esses recibos após exclusão de uma questão e registra o último status notificado por e-mail. Em rollback, volte o código à revisão anterior, sem apagar recibos ou restaurar dados antigos sobre envios recentes.

Importações, montagem/exportação de provas e alterações de destinos exigem usuário administrador. Submissão individual permanece pública. Respostas da IA são rascunhos e precisam de revisão pedagógica e factual humana.

## Decisões e limites

### Grade de Medicina 2026.2 e arquivo histórico

A grade em `questoes/data/grade_2026_2.json` preserva os nove blocos originais do Quadro 1 (páginas 2–4), com 62 entradas e 50 questões por bloco. `questoes/catalog.py` reúne os blocos em cinco períodos (2º, 4º, 5º, 6º e 8º), com 50 componentes compartilhados. Nomes repetidos dentro do mesmo período utilizam um único banco de questões, indicam todas as turmas atendidas e adotam a maior meta solicitada. O 5º período inclui somente A e C, conforme o edital; componentes de períodos diferentes continuam separados, como em 2026.1.

Após backup consistente e aplicação das migrações até 0014, execute:

```sh
python manage.py configurar_semestre_2026_2 --dry-run
python manage.py configurar_semestre_2026_2
```

O comando é atômico e pode ser repetido sem duplicações. A simulação desfaz todas as alterações. Cadastros inesperados impedem a consolidação. Registros de componentes existentes são reaproveitados; entradas repetidas passam a apontar para o cadastro compartilhado. Os nove blocos anteriores são arquivados pelo campo `Periodo.ativo`, sem exclusão de períodos ou componentes. Questões são vinculadas ao cadastro compartilhado, mantendo seus IDs, conteúdo, status, imagens, alternativas e recibos. Links e formulários abertos antes da consolidação continuam funcionando. O histórico de 2026.1 permanece integralmente preservado e inativo. A unicidade do nome do período continua sendo por semestre.

No gerador, selecione os componentes que comporão cada avaliação. A quantidade começa na maior meta de envio e pode ser reduzida para a prova da turma selecionada, sem alterar a meta ou o banco. Por exemplo, APSC VI atende A/B/C/D com meta de seis questões; a prova C/D pode usar cinco. Quantidades fora de 1 até a meta são rejeitadas no servidor. Selecionar dois links antigos do mesmo componente não duplica o banco na prova.

Os formulários de 2026.2 usam quatro alternativas; asserção-razão mantém a chave padrão de cinco respostas. Questões históricas não são reescritas. A marca pública e administrativa passa a ser QUESTÕES MEDICINA, com as logos existentes `static/images/logo.jpg` (UNIPÊ) e `static/images/naped.jpg` (NAPED), e o crédito de produção no rodapé.

Transações e uma chave única por formulário evitam questões parciais e repetição do mesmo POST, inclusive entre processos e após exclusão administrativa da questão. Um novo formulário ainda permite enviar conteúdo igual intencionalmente. SQLite pode rejeitar gravações concorrentes sob carga: a mensagem preserva o texto e permite tentar novamente.

Notificações administrativas registram o último status entregue para que repetir a ação em lote reenvie apenas as falhas. SMTP não oferece garantia de entrega exatamente uma vez: uma interrupção após envio e antes do registro ainda pode exigir conferência manual.

Chamadas Gemini têm prazo de 60 segundos, sem retry automático pago; navegador aborta após 75 segundos. A resposta é validada antes de preencher o formulário. O SDK legado `google-generativeai` foi mantido para compatibilidade; a migração de SDK/modelos e uma política global de cotas para o endpoint público são trabalhos futuros.

A remoção da credencial SMTP do código não a revoga nem apaga o histórico Git. Ela precisa ser revogada/substituída pelo titular no provedor. O repositório legado também contém um SQLite e relatório de auditoria versionados; a limpeza do histórico deve ser planejada separadamente para preservar dados e referências existentes.


## Correção de CSRF e respostas da IA (2026.2)

O formulário consulta o token CSRF vigente em cada chamada; um bloqueio CSRF devolve JSON com código `csrf_invalido`, sem desativar o middleware de segurança. O navegador renova o token via `GET /app/api/csrf/` (mesma origem, sem cache) e tenta a operação uma única vez **somente** se o servidor rejeitou a primeira chamada por CSRF. Falhas de serviço, sessão ou rede não substituem o conteúdo já digitado. Respostas HTML inesperadas, 404, 405, 415, 502 e 503 são tratadas com mensagens legíveis sem tentar converter HTML em JSON. A rota de geração permanece protegida por CSRF e aceita apenas POST JSON.

A renovação de token não é garantia de funcionamento de serviços externos: é indispensável verificar logs, variáveis de ambiente de produção e disponibilidade do Gemini. A atualização do repositório não publica automaticamente no PythonAnywhere. Depois do backup e deploy, testar uma geração real supervisionada e confirmar o comportamento do token após abrir o formulário em várias abas. O SDK legado, as cotas da IA e o saneamento do banco/CSV previamente versionados exigem intervenções separadas.
