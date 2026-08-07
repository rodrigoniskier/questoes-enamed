// Espera o HTML da página carregar completamente
document.addEventListener('DOMContentLoaded', function() {

    // Função para aplicar os estilos
    function applyInlineStyles() {
        // Encontra o container dos inlines de alternativa
        const inlineGroup = document.getElementById('alternativa_set-group');
        if (!inlineGroup) {
            // Se não encontrar o grupo (ex: página de adicionar, antes de salvar), não faz nada
            return;
        }

        // Encontra todas as linhas de alternativa (formulários inline)
        const rows = inlineGroup.querySelectorAll('tr.dynamic-alternativa_set');

        rows.forEach(row => {
            // Encontra o checkbox 'eh_correta' dentro desta linha
            const checkbox = row.querySelector('input[name$="-eh_correta"]');
            // Encontra o input de texto dentro desta linha
            const textInput = row.querySelector('.field-texto .vTextField');

            // Aplica largura ao input de texto
            if (textInput) {
                textInput.style.width = '90%';
                // Adiciona estilos para melhorar a leitura no tema escuro (opcional)
                textInput.style.backgroundColor = '#fdfdfd';
                textInput.style.color = '#212529';
                textInput.style.border = '1px solid #ced4da';
            }

            // Verifica se o checkbox existe e está marcado
            if (checkbox && checkbox.checked) {
                // Aplica estilo à linha inteira ou às células se o checkbox estiver marcado
                // Aplicar às células (td) costuma ser mais compatível
                const cells = row.querySelectorAll('td');
                cells.forEach(cell => {
                    cell.style.backgroundColor = '#dff0d8'; // Verde claro
                    // cell.style.fontWeight = 'bold'; // Descomente se quiser negrito
                });
                 // Adiciona uma borda esquerda na primeira célula para mais destaque
                if (cells.length > 0) {
                   cells[0].style.borderLeft = '5px solid green';
                }

            } else {
                 // Garante que linhas não corretas não tenham o estilo (caso mude dinamicamente)
                 const cells = row.querySelectorAll('td');
                 cells.forEach(cell => {
                    // Resetar para cor padrão pode ser complexo devido ao tema.
                    // Em vez disso, apenas removemos nosso estilo específico.
                    // Se você precisar resetar explicitamente, terá que detectar o tema (claro/escuro).
                    cell.style.backgroundColor = '';
                 });
                 if (cells.length > 0) {
                    cells[0].style.borderLeft = '';
                 }
            }
             // Adiciona uma borda inferior sutil entre as linhas
            row.style.borderBottom = '1px solid #eee';

        });
    }

    // Aplica os estilos quando a página carrega
    applyInlineStyles();

    // --- Tratamento para novas linhas adicionadas dinamicamente ---
    // O Django Admin usa jQuery para adicionar novos inlines. Vamos observar por mudanças.
    // Usamos MutationObserver para detectar quando novas linhas são adicionadas.
    const inlineGroupObserver = document.getElementById('alternativa_set-group');
    if (inlineGroupObserver) {
        const observer = new MutationObserver(function(mutations) {
            mutations.forEach(function(mutation) {
                if (mutation.addedNodes.length > 0) {
                    mutation.addedNodes.forEach(node => {
                        // Verifica se o nó adicionado é uma linha de formulário
                        if (node.nodeType === 1 && node.matches('tr.dynamic-alternativa_set')) {
                           // Reaplicamos os estilos a esta nova linha específica
                           applyStylesToRow(node);
                        }
                    });
                }
            });
        });

        observer.observe(inlineGroupObserver.querySelector('tbody'), { childList: true });
    }

    // Função auxiliar para aplicar estilo a uma linha específica (usada pelo Observer)
    function applyStylesToRow(row) {
        const checkbox = row.querySelector('input[name$="-eh_correta"]');
        const textInput = row.querySelector('.field-texto .vTextField');

        if (textInput) {
            textInput.style.width = '90%';
            textInput.style.backgroundColor = '#fdfdfd';
            textInput.style.color = '#212529';
            textInput.style.border = '1px solid #ced4da';
        }

        const cells = row.querySelectorAll('td');
        if (checkbox && checkbox.checked) {
            cells.forEach(cell => { cell.style.backgroundColor = '#dff0d8'; });
            if (cells.length > 0) cells[0].style.borderLeft = '5px solid green';
        } else {
             cells.forEach(cell => { cell.style.backgroundColor = ''; });
             if (cells.length > 0) cells[0].style.borderLeft = '';
        }
        row.style.borderBottom = '1px solid #eee';

        // Adiciona um listener para quando o checkbox desta nova linha for alterado
        if(checkbox) {
            checkbox.addEventListener('change', () => applyStylesToRow(row));
        }
    }

    // Adiciona listener para checkboxes existentes mudarem
     const inlineGroupCheckboxes = document.querySelectorAll('#alternativa_set-group input[name$="-eh_correta"]');
     inlineGroupCheckboxes.forEach(checkbox => {
        const row = checkbox.closest('tr.dynamic-alternativa_set');
        if (row) {
            checkbox.addEventListener('change', () => applyStylesToRow(row));
        }
     });

});