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

Transações e uma chave única por formulário evitam questões parciais e repetição do mesmo POST, inclusive entre processos e após exclusão administrativa da questão. Um novo formulário ainda permite enviar conteúdo igual intencionalmente. SQLite pode rejeitar gravações concorrentes sob carga: a mensagem preserva o texto e permite tentar novamente.

Notificações administrativas registram o último status entregue para que repetir a ação em lote reenvie apenas as falhas. SMTP não oferece garantia de entrega exatamente uma vez: uma interrupção após envio e antes do registro ainda pode exigir conferência manual.

Chamadas Gemini têm prazo de 60 segundos, sem retry automático pago; navegador aborta após 75 segundos. A resposta é validada antes de preencher o formulário. O SDK legado `google-generativeai` foi mantido para compatibilidade; a migração de SDK/modelos e uma política global de cotas para o endpoint público são trabalhos futuros.

A remoção da credencial SMTP do código não a revoga nem apaga o histórico Git. Ela precisa ser revogada/substituída pelo titular no provedor. O repositório legado também contém um SQLite e relatório de auditoria versionados; a limpeza do histórico deve ser planejada separadamente para preservar dados e referências existentes.

