# Precisa de Revisões — nomenclatura e feedback

Atualização de 27/10/2026.

## Alterações

A classificação exibida é **Precisa de Revisões** no Django Admin, no painel de
pré-auditoria, nos filtros, no histórico de decisões, no banco de questões
visualizado pelo professor e no histórico carregado durante a submissão.
As ações administrativas e os textos de ajuda seguem essa nomenclatura.

O assunto automático é **Devolutiva pedagógica da questão {número} — Precisa de
Revisões**. O corpo em texto e o corpo HTML utilizam a mesma classificação.
Orientações anteriores que contenham a nomenclatura substituída recebem o novo
vocabulário ao serem apresentadas ou enviadas. O texto histórico armazenado é
preservado; o conteúdo científico das questões não é transformado.

Os códigos internos de status, os recibos de notificações e as migrações antigas
continuam compatíveis com o banco existente. `status_descricao` fornece o nome
atualizado para interfaces e exportações; o campo técnico `status` continua
disponível para integrações existentes. Questões nessa situação continuam fora
dos geradores até nova aprovação humana.

## Verificação do envio

O backend de produção está configurado para SMTP com TLS e autenticação por
credenciais privadas. O envio permanece automático após o commit de uma decisão
humana, tanto no Admin quanto na pré-auditoria. O estado de envio aparece no
histórico. Nenhuma notificação antiga é reenviada pela atualização.

Os testes usam o backend SMTP do Django e um servidor receptor local, com
destinatários fictícios. Eles verificam o caminho POST → decisão persistida →
callback após commit → renderização MIME texto/HTML → aceitação pelo servidor
SMTP → confirmação no histórico. Também verificam parecer longo, escape de HTML,
link de revisão, ausência da nomenclatura antiga no conteúdo apresentado,
repetições sem duplicação, falha de SMTP e retomada por ação explícita.

Validação local: 218 testes Django (os três de navegador executados separadamente
e aprovados), sete testes operacionais, Ruff/formatação, check Django e migrações
sem divergências. A auditoria das dependências não encontrou vulnerabilidades
conhecidas. Foram inspecionadas capturas do formulário no painel e no Admin,
em desktop e celular.

A conexão autenticada com o Gmail no PAW não é verificada a partir deste ambiente.
Para conferi-la sem enviar mensagens e sem modificar registros, depois da
atualização execute no virtualenv da aplicação:

```bash
python manage.py verificar_email_feedback --conectar
```

O comando verifica o backend, a presença de credenciais/remetente e a conexão
autenticada usando as configurações da aplicação. Não imprime as credenciais,
não executa `send_messages` e não modifica decisões. Em caso de falha, retorna
erro sem afirmar que o envio está funcionando. A mensagem esperada é:

```text
Feedback automático por SMTP: configurado.
Remetente e credenciais: presentes (valores não exibidos).
Conexão e autenticação SMTP: OK.
Nenhuma mensagem foi enviada; nenhum registro acadêmico foi alterado.
```

Autenticação bem-sucedida não comprova chegada à caixa de entrada: entrega final,
spam e políticas do provedor só podem ser confirmados em uma notificação real
autorizada. SMTP também não garante entrega exatamente uma vez após uma falha
ambígua; o procedimento de conferência manual permanece documentado no relatório
de pareceres.

## Publicação manual

Migrações: `questoes.0017_alter_questao_comentario_validacao_and_more` e
`pre_auditoria.0011_alter_decisaopreauditoria_acao`. Alteram rótulos e textos de
ajuda, sem atualização em massa dos valores dos registros. Não há dependências
novas.

Use o mesmo procedimento de backup, pull para o SHA aprovado, migrações,
estáticos e Reload manual descrito em [PARECERES_NAPED_2026_2.md](PARECERES_NAPED_2026_2.md),
com o novo SHA informado na entrega. O diagnóstico SMTP é uma etapa adicional
após a atualização. Nenhuma operação foi executada no PythonAnywhere nesta tarefa.
