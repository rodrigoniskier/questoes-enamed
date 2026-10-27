document.querySelectorAll('.institutional-decision').forEach((form) => {
  const text = form.querySelector('[data-parecer]');
  const status = form.querySelector('[name="classificacao"]');
  const counter = form.querySelector('.parecer-count');
  const update = () => {
    const mandatory = ['APROVADA_SUGESTOES', 'REPROVADA'].includes(status.value)
      || (status.value === 'APROVADA' && form.dataset.exigeExcecao === 'true');
    text.required = mandatory;
    text.minLength = mandatory ? 20 : 0;
    counter.textContent = `${Array.from(text.value).length.toLocaleString('pt-BR')} / ${text.maxLength.toLocaleString('pt-BR')} caracteres`;
  };
  text.addEventListener('input', update);
  status.addEventListener('change', update);
  form.querySelector('.parecer-topicos').addEventListener('click', () => {
    const topics = [
      'Texto-base e contextualização', 'Enunciado e comando da questão',
      'Alternativas, afirmativas e distratores', 'Gabarito e consistência científica',
      'Justificativas e fundamentação conceitual', 'Adequação pedagógica e complexidade cognitiva',
      'Recomendações adicionais do avaliador',
    ].map((title) => `${title}:\n`).join('\n');
    const addition = `${text.value ? '\n\n' : ''}${topics}`;
    if (Array.from(text.value + addition).length > text.maxLength) return;
    text.value += addition;
    text.focus();
    update();
  });
  update();
});
