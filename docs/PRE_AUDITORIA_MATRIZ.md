# Matriz de rastreabilidade da pré-auditoria

Fonte analisada: *Manual de Elaboração de Provas do Curso da Medicina*, ISBN 978-65-86735-62-8, especialmente as seções 2, 3.1–3.4 e Apêndice B (páginas impressas 4–28). O PDF privado fornecido no workspace não é distribuído com o código.

As referências de seção abaixo usam a numeração impressa no manual. Uma pendência sem evidência literal no conteúdo não deve ser criada pela IA. A presença de uma recomendação não equivale a reprovação. Critérios que dependem de catálogo institucional, autoria ou informação ausente são inconclusivos até conferência humana.

| ID | Critério do manual | Seção | Validação implementada / planejada | Teste automatizado |
| --- | --- | --- | --- | --- |
| 01 | Texto-base relacionado ao tema/objetivos e a uma situação-problema | 3.1.1; 3.3.1; Ap. B.1 | Objetivos da encomenda ausentes ficam inconclusivos; aprovação exige conferência registrada com referência | `test_approval_requires_human_evidence_and_updates_official_status` |
| 02 | Texto-base claro, preciso e necessário | 3.1.1; 3.3.5–7; Ap. B.2 | IA semântica; ausência do campo é regra determinística | `test_clean_structural_result_is_inconclusive_not_approval` |
| 03 | Imagem pertinente e de boa qualidade, quando houver | 3.1.1; Ap. B.3 | Imagem existente fica inconclusiva para inspeção humana; evidência vinculada ao parecer | Revisão visual humana registrada na UI |
| 04 | Comando claro, direto e preciso | 3.1.2; Ap. B.4 | IA semântica e campo não vazio determinístico | `test_clean_structural_result_is_inconclusive_not_approval` |
| 05 | Opções de resposta não excessivamente longas | 3.2.1; Ap. B.5 | Gemini semântico; evidência precisa citar literalmente o conteúdo; recomendação não bloqueia sozinha | `test_semantic_recommendation_is_not_a_blocking_pendency` |
| 06 | Evitar repetição de palavras do texto-base nas opções | 3.2.1–3.2.2; Ap. B.6 | Gemini semântico; duplicação integral de opção também determinística | Achados semânticos são validados em `test_semantic_recommendation_is_not_a_blocking_pendency` |
| 07 | Ordem lógica, padronização e forma trapezoidal | 3.2.1; Ap. B.7 | Gemini apresenta recomendação, sujeita ao revisor | Mesmo contrato JSON semântico |
| 08 | Evitar absolutos e negativos que deem pistas | 3.2.1–3.2.2; Ap. B.8 | Gemini pode classificar recomendação/condicional; palavra isolada não reprova | Mesmo contrato JSON semântico |
| 09 | Distratores plausíveis, sem pegadinha | 3.1.3; 3.2.1; Ap. B.9 | Gemini sugere; validade clínica permanece sujeita a especialista | `test_direct_personal_identifiers_block_paid_semantic_call` |
| 10 | Referências oriundas do acervo físico/digital da biblioteca | 3.1.1; 3.4.1; Ap. B.10 | Inconclusivo até conferência no acervo; revisor registra citação/referência; exige referência ao aprovar | `test_approval_requires_human_evidence_and_updates_official_status` |
| 11 | Autoria, confiabilidade e norma bibliográfica | 3.1.1; 3.3.3–4; Ap. B.11 | Inconclusivo; referência consultada obrigatória, sem alegar atualidade/validade factual | Confirmação documental registrada no mesmo teste de aprovação |
| 12 | Identificar elaboração própria do texto-base | 3.3.4; Ap. B.12 | Inconclusivo; referência/declaração deve ser registrada pelo revisor | Confirmação documental registrada no mesmo teste de aprovação |
| 13 | Classificação de Bloom | 2.1; 3.4.1; Ap. B.13 | Inconclusivo; classificação e documento de apoio registrados pelo revisor | Confirmação documental registrada no mesmo teste de aprovação |
| 14 | Cinco opções para resposta única | 3.2.1; Ap. B.14 | Política local por componente via `alternative_count`; 2026.2 aceita quatro; históricos não são alterados | `test_semester_2026_2_single_answer_uses_four_options` |
| 15 | Paralelismo sintático e semântico | 3.2.1; Ap. B.15 | Gemini semântico apenas para resposta única | Contrato JSON semântico e teste de recomendação |
| 16 | Coesão, coerência e concordância comando/opções | 3.2.1; Ap. B.16 | Gemini semântico apenas para resposta única; requer revisão linguística | Contrato JSON semântico |
| 17 | Afirmações completas, independentes e não excludentes | 3.2.2; Ap. B.17 | Gemini semântico apenas para resposta múltipla | Contrato JSON semântico |
| 18 | Ao menos uma afirmação correta e uma incorreta | 3.2.2; Ap. B.18 | Determinístico por contagem do gabarito | `test_multiple_choice_requires_true_and_false_statements` |
| 19 | Chave de combinações equilibrada e em ordem crescente | 3.2.2; Ap. B.19 | Inconclusivo; conferência humana com referência e justificativa é registrada antes da aprovação | Evidência persiste e fica associada à versão do parecer |
| 20 | Proposições de asserção-razão vinculadas ao contexto/objetivos | 3.2.3; Ap. B.20 | Gemini verifica relação interna; objetivos externos ausentes continuam inconclusivos | Critério é enviado somente para asserção-razão |
| 21 | Duas proposições simples e não longas | 3.2.3; Ap. B.21 | Segunda proposição é determinística; extensão/clareza é semântica | Chave padrão validada em `test_assertion_reason_requires_the_canonical_five_response_key` |
| 22 | Proposições analisáveis independentemente | 3.2.3; Ap. B.22 | Gemini semântico apenas para asserção-razão | Critério é enviado somente para asserção-razão |
| 23 | Item discursivo com texto-base e enunciado coerentes | Ap. B.23 | Fora do escopo: o sistema suporta apenas os três tipos objetivos | Não aplicável nesta entrega |
| 24 | Subtarefas, aspectos avaliados e pontuação explícitos | Ap. B.24 | Fora do escopo; motor mantém IDs extensíveis | Não aplicável nesta entrega |
| 25 | Evitar perguntas discursivas que aceitem sim/não ou quem/quando/onde | Ap. B.25 | Fora do escopo; motor mantém IDs extensíveis | Não aplicável nesta entrega |
| 26 | Dimensionar tarefas ao tempo de resposta | Ap. B.26 | Fora do escopo; motor mantém IDs extensíveis | Não aplicável nesta entrega |

## Estado dos 12 blocos da missão

| Bloco | Estado comprovável | Lacuna remanescente |
| --- | --- | --- |
| 1. Preparação | Branch criada; PDF analisado e excluído localmente; banco de testes isolado | Não há acesso à produção nem ao PythonAnywhere |
| 2. Aplicativo | App, rota, permissões, filtros ativos, indicadores, paginação, seleção de aprovação e lotes de auditoria persistidos | O navegador conduz os passos; não há worker independente se a página for fechada |
| 3. Scanning | Regras estruturais dos três tipos e Gemini JSON pelo controle de quota/timeout existente; UUID estável por lote/questão/versão | Uma chamada síncrona por questão; sem teste de integração ao Gemini real; bloqueio conservador de PII não detecta todos os dados sensíveis |
| 4. Manual | Critérios 1–22 mapeados; 23–26 registrados como discursivos fora do escopo; referências humanas exigidas | Não existe validação clínica nem conexão com catálogo da biblioteca |
| 5. Parecer | Pendências e inconclusivos persistidos; evidências imutáveis por versão | Comparação lado a lado com sugestão editorial da IA não existe; aprovação depende de confirmar todos os inconclusivos |
| 6. Edição | Edição completa dos campos atuais, alternativas, imagem e comentário, snapshot, conflito por fingerprint e reavaliação estrutural | Sugestões não são aplicadas automaticamente nem há interface de comparação original × sugestão |
| 7. Professor | Link assinado, expirável, nonce de uso único, questão limitada, sem aprovação e retorno à fila | Entrega SMTP exata uma vez não pode ser garantida; erro requer conferência/reenvio explícito |
| 8. Aprovação | Aprovação individual/em lote, lock, fingerprint, validação, decisão atômica e `Questao.status` como fonte oficial | SMTP pode falhar após salvar aprovação; verificação operacional continua necessária |
| 9. UX | Shell responsivo, navegação institucional, mensagens, filtros, lote máximo 25, progresso persistido e retomada | Uma questão por chamada; fechamento da página pausa o lote até retomada manual |
| 10. Testes | Testes isolados de permissões, Gemini simulado, PII, edição, link, aprovação e processamento incremental | Concorrência real entre processos e regressões de e-mail SMTP não simulam hospedagem |
| 11. Deploy | Script com backup SQLite consistente, mídia/config privada, integrity check, row comparison e rollback documentado | Não executado em produção; smoke test HTTP e Reload precisam do proprietário |
| 12. GitHub | Código e docs locais preparados para revisão | Commit, push, PR e CI remota ainda pendentes |

O status definitivo continua em `Questao.status`. Uma aprovação só é permitida após resolver pendências obrigatórias e registrar evidência para cada critério inconclusivo; critérios bibliográficos exigem referência consultada. Os critérios 23–26 permanecem fora do escopo do produto e não são tratados como verificados.