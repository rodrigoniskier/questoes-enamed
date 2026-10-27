# Pareceres institucionais do NAPED — 27/10/2026

## Diagnóstico confirmado

O relato e as capturas foram conferidos com o exemplo de parecer técnico enviado.
A questão 1821 foi usada somente como cenário de reprodução: nenhum dado de
produção foi acessado ou alterado.

1. `PedidoCorrecaoForm.mensagem` e o textarea do painel tinham máximo de **2.000
   caracteres**. Um parecer de 2.256 caracteres ultrapassava esse limite. A view
   substituía qualquer erro do formulário pela mensagem de mínimo de 20
   caracteres, inclusive quando o erro real era de comprimento máximo.
2. `QuestaoRevisaoForm` exigia texto-base preenchido. Esvaziá-lo para transferir
   conteúdo ao enunciado impedia o salvamento. Agora essa reorganização pode ser
   salva e o resultado estrutural correspondente fica explícito.
3. Nas edições do revisor e do professor, o snapshot “antes” era capturado depois
   de `ModelForm.is_valid()`, que já altera a instância em memória. Uma edição
   somente textual podia persistir sem gerar histórico e sem disparar a nova
   verificação estrutural. A captura ocorre agora antes da validação; a
   reavaliação usa o objeto recarregado do banco.
4. O Admin alterava status e notificava por um caminho diferente da pré-auditoria.
   As decisões e notificações passam pelo mesmo serviço.
5. O montador manual e a consulta de gabarito por IDs podiam selecionar itens sem
   filtrar aprovação. Ambos passam a aceitar exclusivamente os dois resultados
   aprovados.
6. A rota antiga de edição manual tinha status em letras minúsculas, envio de
   e-mail próprio e validação limitada. Agora encaminha para o Admin oficial,
   com verificação de permissão de alteração.
7. A conferência real no navegador evidenciou a conversão de quebras de linha em
   CRLF no POST. A normalização para LF ocorre antes dos validadores; um texto
   de 20.000 caracteres com parágrafos usa o mesmo limite na tela e no servidor.

## Comportamento entregue

| Situação oficial | Parecer | Disponibilidade em provas | Comunicação |
| --- | --- | --- | --- |
| `APROVADA` | Aprovação sem sugestões obrigatórias | Disponível | E-mail de aprovação |
| `APROVADA_SUGESTOES` | Aprovação com sugestões de ajustes | Disponível, independentemente da adoção das sugestões | E-mail colaborativo, com autonomia docente explícita |
| Precisa de Revisões | Problemas impeditivos e encaminhamento para revisão | Indisponível | Devolutiva pedagógica com justificativa completa |
| `PENDENTE` | Etapa de tramitação; sem decisão conclusiva | Indisponível | Sem notificação de parecer conclusivo |

O campo de status existente foi preservado. Não houve reclassificação em massa.
`CORRECAO`, resultados estruturais antigos e decisões anteriores continuam
armazenados com seus valores originais. O diagnóstico estrutural “Apta para
aprovação” continua separado da decisão humana de aprovação.

No painel, **Registrar parecer** oferece as três decisões e a opção de tramitação.
Há filtros por situação e contadores que incluem aprovadas com sugestões. No
Admin, o status oficial é somente leitura na edição de conteúdo; o link
**Registrar ou reavaliar parecer** abre o mesmo formulário institucional.
Também estão disponíveis ações de classificação em lote. Nelas, sugestões e
classificações como Precisa de Revisões usam as orientações já registradas em `comentario_validacao` e
respeitam as mesmas validações do serviço.

Os pareceres aceitam **20.000 caracteres**, com parágrafos, contador e tópicos
opcionais de apoio derivados da organização do exemplo do NAPED. O limite contém
o tamanho de cada submissão e mensagem, mantendo espaço para pareceres muito
mais extensos que o relato original. O mínimo de 20 caracteres aplica-se a
sugestões e classificações como Precisa de Revisões. Aprovação simples e pendência permitem texto vazio.
Os erros específicos são exibidos e o formulário preserva o texto submetido.

As aprovações exigem verificação estrutural atual. Foi mantida a regra existente
de aprovação excepcional com pendências: exige justificativa e confirmação
humana, registra a excepcionalidade e continua bloqueando gabaritos que não
possam ser representados corretamente na prova. O formulário explica essa
situação. Nenhuma decisão passa a ser tomada automaticamente por IA.

O parecer institucional novo fica em `DecisaoPreAuditoria.justificativa`, sem
sobrescrever comentários históricos. Cada decisão guarda autor, nome no momento
do registro, data, versão do conteúdo e estado da notificação. A mudança de
conteúdo pelo Admin também gera histórico e verificação estrutural.

As três mensagens de e-mail têm versões de texto e HTML, identificam questão,
docente e componente, preservam o parecer completo e usam a configuração de
remetente existente. O HTML escapa o conteúdo do avaliador. Nenhuma mensagem real
é enviada pelos testes.

Quando há verificação estrutural correspondente ao conteúdo que precisa de revisões, a
devolutiva inclui um link assinado de revisão, com prazo de sete dias e uso
único. Alterações posteriores ou uma nova decisão invalidam o acesso anterior.
O reenvio do professor registra o conteúdo anterior e o novo, mantém a necessidade de revisões
no histórico e devolve a questão à pendência para nova análise humana. Sem uma
verificação estrutural correspondente, o e-mail orienta contato com a equipe;
o Admin permite executá-la antes da emissão do parecer.

## Integridade, notificações e segurança

As alterações relacionadas são transacionais. `ReciboDecisao` guarda cada token
de submissão, inclusive os tokens de pedidos equivalentes a uma decisão já
registrada. Repetir um envio não duplica decisões nem reverte uma decisão mais
recente. Uma nova versão ou um parecer diferente pode gerar nova decisão.

O e-mail é iniciado após o commit. O recibo é reservado antes da chamada SMTP,
evitando que dois processamentos enviem simultaneamente a mesma notificação.
As falhas são rastreáveis e podem ser retomadas por ação explícita do avaliador,
repetindo a decisão correspondente. Registros históricos recebem o marcador
`LEGADO` e não são reenviados. Decisões superadas não são notificadas.

Um processo interrompido após iniciar o envio permanece em `ENVIANDO`: não há
retentativa automática, porque SMTP não oferece garantia de entrega exatamente
uma vez. Confirme o recebimento e os logs antes de qualquer regularização
administrativa desse caso. Uma falha no registro de envio após o commit não
transforma a decisão salva em uma falsa falha de gravação.

Foram revisados os arquivos alterados e os caminhos chamados por eles, com foco
em autorização, CSRF, escape de HTML, integridade de versão, consumo de links,
duplicidade, rollback e não exposição de conteúdo ou credenciais nos logs.
Os testes de abuso cobrem esses controles. A auditoria das dependências usa
`pip-audit`; a integração especializada Codex Security não disponibilizou seus
helpers/referências neste ambiente, portanto não foi produzido um scan formal
ou SARIF dessa integração.

## Principais arquivos

| Arquivo | Finalidade |
| --- | --- |
| `questoes/avaliacao.py` | Serviço comum de decisões humanas e notificações |
| `questoes/models.py` | Nova opção de aprovação com sugestões e conjunto de status aprovados |
| `pre_auditoria/models.py` | Histórico das novas decisões, recibos e acompanhamento de e-mail |
| `pre_auditoria/forms.py` | Parecer de 20.000 caracteres, normalização e validação específica por decisão |
| `pre_auditoria/views.py` | Formulário institucional, edição corrigida, filtros e reenvio do professor |
| `questoes/admin.py` | Decisões integradas ao Admin e histórico das edições |
| `questoes/utils.py`, `questoes/templates/questoes/emails/` | Assuntos e devolutivas em texto e HTML |
| `questoes/views.py` | Aprovação com sugestões nos geradores e exclusão de questões que precisam de revisões do montador/gabarito |
| `questoes/static/questoes/parecer.js`, `pre_auditoria.css` | Contador, tópicos opcionais e formulário responsivo |
| `pre_auditoria/test_pareceres.py`, `test_migrations_pareceres.py` | Regressão dos relatos, segurança e preservação histórica |
| `pre_auditoria/test_browser.py` | Salvamento e emissão de pareceres nas duas interfaces em desktop e celular |
| `.github/workflows/quality.yml` | Mantém os checks e disponibiliza capturas dos testes com dados sintéticos |
| `ops/deploy_importacao.py` | Mantém o procedimento de backup/pull/migração; checksum compatível com Python 3.10 |

## Validação reproduzível

Resultados locais: **209 testes Django**, com os três testes de navegador
executados separadamente e aprovados; **sete testes operacionais** aprovados;
Ruff, formatação, check do Django e verificação de migrações aprovados.
`pip-audit -r requirements.txt`: nenhuma vulnerabilidade conhecida encontrada.

```bash
python -m ruff check questoes bulk_submit gerador_provas pre_auditoria ops/deploy_importacao.py ops/test_deploy_importacao.py ops/verify_integrity.py
python -m ruff format --check questoes bulk_submit gerador_provas pre_auditoria ops/deploy_importacao.py ops/test_deploy_importacao.py ops/verify_integrity.py
python -m unittest ops.test_deploy_importacao
python manage.py test --settings=gerador_provas.test_settings
python manage.py makemigrations --check --dry-run --settings=gerador_provas.test_settings
python manage.py check --settings=gerador_provas.test_settings
python -m pip_audit -r requirements.txt
RUN_BROWSER_TESTS=1 BROWSER_EXECUTABLE=/caminho/do/chromium python manage.py test questoes.test_importacao_browser pre_auditoria.test_browser --settings=gerador_provas.test_settings
```

Os testes incluem pareceres de 2.256, 6.001 e 20.000 caracteres, inclusive o limite
com CRLF; mínimo, máximo e preservação do texto; decisões e transições inválidas;
tentativas sem permissão e sem CSRF; escape de conteúdo malicioso; duplicidade e
replay após decisões posteriores; falhas de persistência e SMTP; preservação dos
status e pareceres legados ao aplicar as migrações; edição sem alterar
alternativas; transferência de campos; reabertura, prévia e estrutura atualizada;
reenvio do professor; filtros, contadores e disponibilidade nos geradores.

Os testes de navegador usam telas de 1440 e 390 pixels, conferem ausência de
overflow e erros de JavaScript, edição real, emissão de pareceres nas duas
interfaces e preservação do fluxo de importação anterior. As capturas foram
inspecionadas visualmente. Configurações isoladas usam SQLite de teste, e-mail
em memória e IA simulada ou desabilitada.

## Migrações e publicação manual no PythonAnywhere

Novas migrações:

- `questoes.0016_alter_questao_status`;
- `pre_auditoria.0010_decisaopreauditoria_email_enviado_em_and_more`.

São ampliações de campos/opções, novos campos e criação da tabela de recibos.
Não incluem exclusão ou transformação de registros acadêmicos nem envio de
notificações. Não houve alteração de dependências nesta entrega.

Repositório: `rodrigoniskier/questoes-enamed`, branch principal `main`, branch de
trabalho `fix/pareceres-naped-edicao`. Base inspecionada:
`c0e115bf9f975cb196d602c7dbe759c4d448f09f`. O PR, a revisão integrada e o resultado
do CI devem ser conferidos na entrega. Não foram executadas operações no PAW.

Os caminhos abaixo constam da documentação anterior de implantação. Confirme o
virtualenv no painel Web antes de executar; o servidor não foi acessado nesta
tarefa. Escolha uma janela sem novas submissões/edições durante o backup,
migrações e conferência de integridade.

Use o SHA de integração informado na entrega em `NAPED_APPROVED_SHA`. O helper é
extraído dessa revisão imutável para fora do checkout antes do pull:

```bash
cd /home/rodrigoniskier/prova-naped
source /home/rodrigoniskier/.virtualenvs/myenv/bin/activate
NAPED_APPROVED_SHA=SHA_DE_INTEGRACAO_INFORMADO_NA_ENTREGA
git fetch origin main
git show "${NAPED_APPROVED_SHA}:ops/deploy_importacao.py" > /home/rodrigoniskier/deploy_pareceres.py
python /home/rodrigoniskier/deploy_pareceres.py \
  --checkout /home/rodrigoniskier/prova-naped \
  --expected-before "$(git rev-parse HEAD)" \
  --approved-sha "$NAPED_APPROVED_SHA" \
  --wsgi /var/www/rodrigoniskier_pythonanywhere_com_wsgi.py \
  --backup-dir /home/rodrigoniskier/.private_backups/pareceres-naped
```

Esse comando manual valida conta, virtualenv, checkout limpo, remote, revisão,
WSGI e banco; faz backup SQLite consistente, mídia, configuração privada e
código; executa `git pull --ff-only` para a revisão aprovada, aplica migrações e
`collectstatic`, compara os registros preexistentes com o backup e atualiza
`RELEASE_REVISION`. Só instala dependências se o arquivo correspondente mudou
desde a versão atualmente instalada. O procedimento não executa Reload.

Após o resultado `CODIGO_BANCO_E_ESTATICOS_VERIFICADOS_RELOAD_PENDENTE`, abra
**Web → rodrigoniskier.pythonanywhere.com → Reload**. Confirme:

1. `/app/api/version/` identifica o SHA integrado.
2. Login e abertura do painel e do Admin funcionam sem erros.
3. O formulário exibe as três classificações e a tramitação pendente.
4. Em item de homologação autorizado, o parecer longo é salvo integralmente.
5. A transferência de texto-base para enunciado persiste ao reabrir e gera
   histórico e nova estrutura.
6. Os logs não apresentam erros de banco, migração, estáticos ou SMTP.

Uma gravação de decisão pode enviar e-mail real nesse ambiente: use apenas uma
questão de homologação com destinatário autorizado. A questão 1821 não deve ser
alterada como teste.

## Recuperação

Se o helper interromper, não faça Reload nem execute reset ou restauração às
cegas. O recibo privado registra `previous_sha`, `approved_sha`, tipo do erro e
etapas concluídas. Preserve esse recibo e o backup.

Para um problema somente de código, prefira reverter o commit de integração por
um novo commit no GitHub e manter as migrações aditivas. Confirme antes que não
existam questões com `APROVADA_SUGESTOES`: versões antigas não reconhecem esse
status nos geradores. Nesse caso, mantenha a aplicação em manutenção e prepare
uma correção compatível em vez de apagar ou reclassificar decisões.

Se for indispensável restaurar o banco, interrompa gravações, faça também uma
cópia do estado pós-falha e restaure o conjunto consistente de banco/código/
configuração/mídia do backup privado. Não restaure o SQLite antigo sobre novas
submissões sem reconciliá-las: isso descartaria dados. Reconfira integridade e
registros antes do Reload. A recuperação não é automática nem destrutiva.
