# Correção de cartões de respostas fotografados com ChatGPT Work

## Antes da prova

1. Gere a avaliação e use **Imprimir somente cartão de respostas** para obter a(s) folha(s) A4. O cartão é individual: cada aluno preenche nome, RGM, turma e, somente na ausência do RGM, CPF.
2. Baixe **Gabarito CSV para correção por IA** no mesmo navegador. Não edite a ordem das questões/alternativas depois da impressão. O código de 12 caracteres do cartão precisa coincidir exatamente com o código do CSV.
3. Use a versão definitiva da prova para toda a turma. Não misture cartões de versões diferentes sem exportar os respectivos CSVs.
4. Para a fotografia, utilize boa iluminação, folha plana, câmera perpendicular e resolução suficiente para ler os números das questões, os círculos e a identificação. Inclua as quatro marcas pretas dos cantos. Capture uma folha inteira por imagem, sem recortes nem sombras. O aluno deve preencher completamente **uma única bolha** por questão. Um X ou um risco é menos confiável.
5. Reúna aproximadamente 50–60 imagens em um PDF, mantendo uma fotografia por página, preferencialmente a 200–300 dpi ou superior. Se a prova tiver mais de 120 questões, são necessárias várias folhas por aluno: fotografe todas e preserve a numeração de folha.

## No ChatGPT Work

Anexe dois arquivos: o **PDF com os cartões** e o **gabarito CSV exportado**. Configure esforço de raciocínio alto/extra-alto, se disponível. Use o roteiro:

> Corrija os cartões de respostas neste PDF com base **exclusivamente** no CSV de gabarito anexo. O campo `codigo_prova` do CSV e o código impresso em cada folha precisam coincidir. **Não invente ou complete respostas**. Leia todas as páginas, uma por uma, registrando a origem exata de cada identificação e marcação. Cada questão tem as opções permitidas indicadas no campo `alternativas_validas`; uma única bolha completamente preenchida indica resposta. Identifique nome completo, RGM e turma; use CPF somente quando não houver RGM. Preserve zeros iniciais de RGM e CPF. Para cada aluno, una folhas adicionais do mesmo cartão apenas se houver identificação e código compatíveis.
>
> Compare as respostas com o CSV, na mesma numeração de questões. Considere em branco e dupla marcação **0 ponto**, registrando a ocorrência. Se um círculo estiver apagado, rasurado, marcado de modo duvidoso, cortado ou ilegível, NÃO escolha uma letra provável: classifique como **REVISÃO MANUAL**. Também classifique para revisão dados identificadores ilegíveis, repetidos ou divergentes, ou código/numeração incompatível. Não atribua nota final a aluno com marcações ambíguas ou folha faltante. Não associe páginas a um aluno apenas por proximidade, sem conferência da identificação.
>
> Nas respostas decidíveis, atribua peso igual a todas as questões e calcule a nota na escala **0 a 10**, com duas casas decimais: `10 * acertos / total_questoes`. Gere um arquivo **.xlsx** com as abas:
>
> **Notas:** página(s) do PDF, nome completo, RGM (texto), CPF somente quando necessário (texto), turma, código da prova, total de questões, acertos, erros, em branco, múltipla marcação, nota, situação (`CONFERIDO` ou `REVISÃO MANUAL`).
>
> **Auditoria:** página, identificador do aluno, número da questão, resposta lida, gabarito esperado, decisão (`ACERTO`, `ERRO`, `EM BRANCO`, `DUPLA MARCAÇÃO`, `AMBÍGUA`) e observação; inclua todas as questões de cada aluno, preservando a trilha de conferência.
>
> **Pendências:** todas as páginas/questões que precisam ser verificadas no papel, motivo, e o que faltou ler. Identifique cartões duplicados e alunos com folhas incompletas. Antes de entregar, verifique se todas as páginas do PDF foram tratadas e se cada aluno teve exatamente o número esperado de questões. Não preencha notas ou identificadores a partir de suposições. Faça uma checagem cruzada das respostas lidas com as imagens e do total de acertos com o gabarito CSV.

## Conferência necessária

A IA visual é auxiliar, não um leitor OMR certificado. Compare uma amostra de cartões corrigidos com o papel e **revise manualmente todos os casos sinalizados**, principalmente campos manuscritos, correções de marcação e imagens ruins. Faça a validação final das notas antes do lançamento oficial.

**Proteção dos dados:** prefira RGM e evite fotografar ou compartilhar CPF quando não for necessário. O CSV de gabarito não contém dados pessoais; já o PDF dos alunos e a planilha gerada contêm dados identificáveis e devem permanecer em ambiente institucional autorizado, com acesso restrito e retenção limitada.
