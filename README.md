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

A grade em `questoes/data/grade_2026_2.json` foi transcrita do Quadro 1 (páginas 2–4) das orientações da Prova Integrada 2026.2: nove grupos de períodos/turmas, 62 componentes e 50 questões por prova. Turmas com matrizes ou metas diferentes têm cadastros próprios. O 5º período inclui somente A e C, conforme o edital.

Após backup consistente e aplicação da migração 0013, execute:

```sh
python manage.py configurar_semestre_2026_2 --dry-run
python manage.py configurar_semestre_2026_2
```

O comando é atômico e pode ser repetido sem duplicações. A simulação desfaz todas as alterações. Cadastros inesperados ou duplicados em 2026.2 impedem a ativação. Somente 2026.1 é marcado como inativo; seus períodos, componentes, questões, alternativas e vínculos são preservados e continuam disponíveis na administração por filtro de semestre. Links antigos não permitem novos envios ou geração de rascunhos para semestres arquivados. A unicidade do nome do período passa a ser por semestre, permitindo reutilizar os mesmos nomes sem mover registros históricos.

Os formulários de 2026.2 usam quatro alternativas; asserção-razão mantém a chave padrão de cinco respostas. Questões históricas não são reescritas. A marca pública e administrativa passa a ser QUESTÕES MEDICINA, com as logos existentes `static/images/logo.jpg` (UNIPÊ) e `static/images/naped.jpg` (NAPED), e o crédito de produção no rodapé.

Transações e uma chave única por formulário evitam questões parciais e repetição do mesmo POST, inclusive entre processos e após exclusão administrativa da questão. Um novo formulário ainda permite enviar conteúdo igual intencionalmente. SQLite pode rejeitar gravações concorrentes sob carga: a mensagem preserva o texto e permite tentar novamente.

Notificações administrativas registram o último status entregue para que repetir a ação em lote reenvie apenas as falhas. SMTP não oferece garantia de entrega exatamente uma vez: uma interrupção após envio e antes do registro ainda pode exigir conferência manual.

Chamadas Gemini têm prazo de 60 segundos, sem retry automático pago; navegador aborta após 75 segundos. A resposta é validada antes de preencher o formulário. O SDK legado `google-generativeai` foi mantido para compatibilidade; a migração de SDK/modelos e uma política global de cotas para o endpoint público são trabalhos futuros.

A remoção da credencial SMTP do código não a revoga nem apaga o histórico Git. Ela precisa ser revogada/substituída pelo titular no provedor. O repositório legado também contém um SQLite e relatório de auditoria versionados; a limpeza do histórico deve ser planejada separadamente para preservar dados e referências existentes.

