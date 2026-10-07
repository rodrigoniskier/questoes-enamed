# Engenharia do Questões ENAMED

Antes de editar: entender requisitos, estado do repositório, dados de produção e critérios de aceite. Trabalhar em branch e preferir a menor solução que resolva o comportamento inteiro. Sem microserviços, camadas ou dependências sem necessidade demonstrada.

Validar entradas no servidor, incluindo gabaritos, contagens, identificadores e respostas de IA. Não confiar em inputs ocultos, checklists do navegador ou serviços externos. Falhas devem ter mensagem compreensível, diagnóstico nos logs e recuperação possível. Não expor segredos nem dados pessoais nos logs ou URLs.

Gravações relacionadas devem ser atômicas; submissões repetidas usam recibos únicos duráveis. Preservar o estado do usuário em falhas, limitar chamadas externas e evitar retries automáticos de operações pagas. Não substituir conteúdo preenchido sem confirmação.

Testar comportamentos felizes e adversos: duplicação, rollback, entradas inválidas, isolamento de sessão, permissões e falhas de integrações. IA e SMTP simulados nos testes. Usar configurações isoladas, nunca o banco ou credenciais de produção. Formatar código e executar lint, testes, checagem de migrações, auditoria de dependências e revisão visual responsiva.

Antes de deploy: backup consistente do SQLite, preservar configuração privada e arquivos de mídia, confirmar revisão remota, instalar dependências, aplicar migrações aditivas, coletar estáticos e recarregar. Validar aplicação e integridade dos registros existentes. Não confundir um pull bem-sucedido com deploy validado.

Documentar decisões proporcionais e limites reais. Este padrão reduz risco; não constitui promessa de ausência de defeitos nem validação clínica dos itens gerados.
