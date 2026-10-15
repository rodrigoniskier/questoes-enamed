# Política operacional da pré-auditoria

Esta política usa exclusivamente as orientações visíveis nos formulários de submissão do aplicativo Submeter. Ela não incorpora critérios de manuais institucionais, parâmetros do assistente de rascunho por IA, objetivos curriculares ou documentação bibliográfica.

A pré-auditoria é uma verificação formal. Ela não substitui a avaliação técnica, científica ou pedagógica, que é decisão do gestor responsável.

## Nível A: falhas estruturais obrigatórias

Uma falha estrutural aparece como pendência objetiva, que normalmente deve ser corrigida antes da aprovação. O gestor pode, excepcionalmente, aprovar após inspeção visual e justificativa registrada; essa decisão não altera o resultado nem os achados do parecer.

| Tipo | Validações estruturais |
| --- | --- |
| Todas as questões | Texto-base, enunciado/proposições e justificativa preenchidos; alternativas/afirmativas preenchidas; sem alternativas completamente duplicadas; estrutura compatível com o tipo; gabarito válido. |
| Resposta única | Quantidade de alternativas conforme a configuração do componente e semestre; exatamente uma alternativa correta. Em 2026.2, quatro alternativas. Regras anteriores válidas são preservadas. |
| Resposta múltipla | De três a cinco afirmativas; ao menos uma marcada como correta e uma como incorreta. |
| Asserção-razão | Proposições I e II preenchidas; uma das cinco respostas padronizadas selecionada; justificativa preenchida. |

## Nível B: recomendações opcionais

Quando solicitada pelo gestor, a análise textual por Gemini pode sugerir pontos para revisão, limitados às orientações do Submeter: clareza, objetividade e contextualização do texto-base; clareza do enunciado; negativas desnecessárias; organização, extensão, paralelismo, termos absolutos e plausibilidade aparente das alternativas; independência das afirmativas de resposta múltipla; clareza das proposições de asserção-razão; e coerência aparente entre justificativa e gabarito.

Recomendações não bloqueiam a aprovação e não exigem justificativa individual. A IA não julga conhecimentos médicos, protocolos, bibliografia ou objetivos curriculares. Se a API falhar, o gestor pode concluir a revisão com base nas validações estruturais e em sua própria avaliação; a falha da IA não equivale a aprovação científica.

## Decisão e histórico

O parecer estrutural atual pode resultar em **Pendências** ou **Apta para aprovação**. Após a decisão do gestor, `Questao.status` é atualizado para `APROVADA`; o Gerador de Provas continua aplicando seus filtros normais. O gestor também pode editar, solicitar correção ao professor, reavaliar e consultar o histórico.

Pareceres sem o marcador da política atual são históricos. Eles não são alterados nem reinterpretados e não habilitam aprovação sob a política nova. É necessária uma nova pré-auditoria, vinculada ao fingerprint atual da questão. Evidências, decisões e registros de edição antigos permanecem preservados.

O gestor pode aprovar uma questão com pendências estruturais após confirmar a inspeção visual e informar uma justificativa objetiva (mínimo de 20 caracteres). O status oficial passa a `APROVADA`, mas o resultado, os indicadores e os achados do parecer permanecem intactos; a decisão registra o responsável, horário, justificativa e marcador de aprovação excepcional. Questões sem um formato suportado pelo Gerador de Provas, sem alternativas legíveis, com versão alterada, sem parecer atual ou fora da política vigente continuam bloqueadas.

Na limpeza de históricos, o parecer mais recente não pode ser selecionado por padrão. Pareceres antigos sem dependências administrativas podem ser excluídos fisicamente após confirmação das contagens; os que tenham evidências, decisões ou solicitações de correção são arquivados, preservando seus relacionamentos. Cada operação registra o usuário, a data e os IDs afetados. A limpeza não exclui questões nem conteúdo acadêmico.
