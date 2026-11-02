# Seleção, PDF e aprovação em lote — 02/11/2026

No painel de pré-auditoria, marque questões individualmente ou use **Selecionar todas desta página**. A seleção compartilhada serve para exportar, aprovar ou iniciar a pré-auditoria. Abrange até 25 questões da página atual; mudar filtros ou página reinicia a seleção. A limpeza dos históricos tem caixas próprias e permanece independente.

**Exportar selecionadas em PDF** abre uma nova aba com as questões na ordem do painel. Clique em **Salvar como PDF**, selecione esse destino na janela de impressão do navegador e use papel A4. O documento contém texto-base, comandos, alternativas, imagens disponíveis, gabaritos e justificativas. É material para revisão do gestor, incluindo respostas. A exportação não muda o status. Arquivos de imagem ausentes são indicados no documento.

**Aprovar selecionadas em lote** abre uma conferência antes de gravar. Somente questões pendentes, de cadastros ativos e com parecer atual podem ser aprovadas. Questões com pendências continuam exigindo inspeção humana e justificativa de pelo menos 20 caracteres. A justificativa, quando preenchida, fica no histórico de cada decisão. A confirmação é explícita e o lote é atômico: uma falha impede a gravação de todo o lote. Notificações usam o fluxo existente, somente após commit.

Seleções e confirmações são assinadas, vinculadas ao usuário/sessão e expiram em 30 minutos. Alteração da questão, componente, status, histórico de decisão ou parecer invalida a confirmação correspondente. Erros preservam o texto digitado; uma confirmação expirada precisa de nova seleção no painel e não é renovada automaticamente.

## Atualização no PythonAnywhere

Esta alteração, sobre a versão `e2a00ba`, não acrescenta migrações, dependências nem arquivos estáticos. Se as atualizações anteriores já estiverem aplicadas, basta atualizar o código na pasta da aplicação:

```sh
cd /home/rodrigoniskier/prova-naped
git pull --ff-only origin main
```

Depois, abra a aba **Web** e clique em **Reload**. O pull não recarrega os processos do PythonAnywhere. Se o servidor ainda não recebeu as migrações da atualização anterior de pareceres, siga primeiro `PARECERES_NAPED_2026_2.md`; não trate um pull isolado como aplicação dessas migrações.

Confira com uma sessão autorizada: selecionar/desmarcar todas, exportar uma seleção, salvar PDF e conferir primeira/última questão, e confirmar um lote de questões pendentes já revisadas. Esse último passo altera dados e envia as notificações normais; deve ser feito pelo gestor com questões que realmente deseja aprovar.

## Verificação reproduzível

```sh
python manage.py test --settings=gerador_provas.test_settings
RUN_BROWSER_TESTS=1 python manage.py test pre_auditoria.test_batch_browser --settings=gerador_provas.test_settings
```

Os testes usam dados sintéticos, SMTP local/simulado e nenhuma chamada de IA paga. Para o segundo comando, instale Playwright no ambiente de desenvolvimento e disponibilize Chromium ou informe `BROWSER_EXECUTABLE`. `BATCH_REVIEW_DIR` habilita capturas de telas e PDFs sintéticos. O CI executa esse fluxo e conserva seus artefatos.
