# Plano de saneamento do histórico público

Banco SQLite e CSV de auditoria foram removidos do rastreamento atual. Isso não remove versões anteriores, forks, clones, referências de PR ou caches.

A auditoria encontrou no SQLite versionado dados de professores, um usuário com hash de senha e 54 sessões, além de conteúdo acadêmico. O CSV tem 2880 linhas de atividades administrativas. Uma senha SMTP de aplicativo constava em configuração histórica e foi confirmada igual à configurada na hospedagem. Valores e identidades não devem ser incluídos neste documento.

1. O titular deve revogar a senha SMTP antiga no Google e configurar outra privadamente no PythonAnywhere; verifique um envio supervisionado. A rotação não ocorre pela remoção do Git. Confira segurança da conta e atividades recentes. O hash de senha do usuário administrativo também exige troca de senha pelo titular e invalidação das sessões antigas; a ausência de chave Django literal detectada não torna os dados publicados seguros.
2. Guarde `git bundle --all`, cópias privadas de todos os refs e backups consistentes da base atual e mídias; verifique recuperabilidade e registre SHAs/refs sem segredos.
3. Em clone espelho isolado, execute uma simulação de `git-filter-repo` para remover `db.sqlite3`, `relatorio_auditoria_admin.csv` e quaisquer outras cópias detectadas. Prepare um arquivo privado de substituições de segredos históricos; não comite esse arquivo. Não remova todo `settings.py`, pois perderia código legítimo.
4. Confira todos os refs e objetos alcançáveis no espelho limpo com um detector de segredos e busca dos caminhos removidos; faça inventário de PRs, tags, releases, clones e referências da hospedagem afetadas. Compare os arquivos atuais para garantir que o saneamento não mude o aplicativo.
5. Apresente o mapa de SHAs anteriores/novos e o impacto aos colaboradores. **Não execute force-push ou reescrita do repositório compartilhado sem autorização específica.** Combine uma janela sem commits e preserve o espelho anterior privadamente.
6. Após autorização e validação, publique os refs planejados, coordene novos clones e alinhe PythonAnywhere preservando seu banco/arquivos privados. Solicite ao GitHub tratamento de caches/refs de PR quando aplicável; versões antigas em clones de terceiros não podem ser apagadas por este procedimento.

Situação: remoção da árvore atual implementada; rotação no provedor, troca da senha administrativa e reescrita compartilhada ainda dependem de execução pelo titular/autorização específica. Nenhum force-push foi autorizado ou executado.
