# Pré-auditoria: PDF e aprovação em lote

Pedido: exportar as questões exibidas e aprovar uma seleção em lote, com Selecionar todas em ambas as ações; preparar atualização para PythonAnywhere.

Baseline: e2a00ba8d3d678e971e28fd6187443ae4039d77f, main remoto em 02/11/2026, árvore local limpa antes da implementação.

Critérios anteriores à implementação:
- A seleção manual e Selecionar todas abrangem apenas a página atual (até 25), respeitando filtros e status; contador e seleção parcial acessíveis.
- A exportação disponibiliza texto completo, alternativas, imagens disponíveis, gabaritos e justificativas da seleção em layout A4. Usa impressão/Salvar como PDF do navegador, sem instalar dependências no servidor. Não altera decisões.
- Somente leitores autorizados exportam; somente revisores autorizados aprovam. Seleção validada no servidor, vinculada à página exibida, usuário e sessão, com expiração.
- A aprovação exige conferência e confirmação explícita; somente pendentes com parecer atual. Pendências exigem justificativa; regras existentes permanecem.
- O lote é atômico: falhas não deixam aprovações parciais. Reenvios não duplicam decisões ou notificações; mudanças de conteúdo/parecer entre telas invalidam a confirmação.
- Filtros e página de origem preservados; erros de confirmação preservam justificativa e seleção.
- Sem migrações, dependências ou novos arquivos estáticos necessários para esta alteração. PythonAnywhere ainda exige Reload após git pull.

Escopo: desenvolvimento e testes locais com dados sintéticos; publicação da alteração no repositório rodrigoniskier/questoes-enamed para git pull autorizado pelo pedido. Não operar banco, SMTP, IA ou serviço de produção.

Papéis: agente principal implementa servidor e testes; corretor ADS implementa templates; auditor independente recebe critérios e diff e tenta refutar. Decisões acadêmicas de aprovação permanecem com o gestor.

Orçamento: uma implementação localizada, suíte de regressão, teste de navegador com PDF e revisão independente; corrigir achados reproduzíveis e repetir somente verificações afetadas. Parada após resultados documentados e integração remota verificada, ou bloqueio explicitamente registrado.
