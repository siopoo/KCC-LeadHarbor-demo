const activeTasks = [...document.querySelectorAll('[data-task-id]')]
  .filter((item) => ['queued', 'running', 'cancelling'].includes(item.dataset.status));

if (activeTasks.length) {
  const poll = async () => {
    let changed = false;
    await Promise.all(activeTasks.map(async (item) => {
      try {
        const response = await fetch(`/api/tasks/${item.dataset.taskId}`);
        const task = await response.json();
        if (task.status !== item.dataset.status) changed = true;
        const bar = item.querySelector('[data-task-progress-bar]');
        const value = item.querySelector('[data-task-progress-value]');
        const label = item.querySelector('[data-task-progress-label]');
        if (bar) bar.value = Number(task.progress) || 0;
        if (value) value.textContent = `${Number(task.progress) || 0}%`;
        if (label && task.progress_label) label.textContent = task.progress_label;
      } catch (_) {}
    }));
    if (changed) window.location.reload();
    else window.setTimeout(poll, 2500);
  };
  window.setTimeout(poll, 2500);
}

document.querySelectorAll('[data-table-scrollbar]').forEach((scrollbar) => {
  const tableScroll = scrollbar.nextElementSibling;
  const track = scrollbar.querySelector('[data-table-scrollbar-track]');
  const table = tableScroll?.querySelector('table');
  if (!tableScroll || !track || !table) return;

  let syncing = false;
  const sizeTrack = () => {
    track.style.width = `${table.scrollWidth}px`;
    scrollbar.hidden = table.scrollWidth <= tableScroll.clientWidth;
  };
  const syncScroll = (source, target) => {
    if (syncing) return;
    syncing = true;
    target.scrollLeft = source.scrollLeft;
    window.requestAnimationFrame(() => { syncing = false; });
  };

  scrollbar.addEventListener('scroll', () => syncScroll(scrollbar, tableScroll));
  tableScroll.addEventListener('scroll', () => syncScroll(tableScroll, scrollbar));
  window.addEventListener('resize', sizeTrack);
  if ('ResizeObserver' in window) new ResizeObserver(sizeTrack).observe(table);
  sizeTrack();
});

document.querySelectorAll('[data-state-picker]').forEach((input) => {
  const options = [...document.querySelectorAll(`#${input.getAttribute('list')} option`)]
    .map((option) => option.value.toLocaleLowerCase());
  const validate = () => {
    const valid = options.includes(input.value.trim().toLocaleLowerCase());
    input.setCustomValidity(valid || !input.value ? '' : input.dataset.invalidMessage);
    return valid;
  };
  input.addEventListener('input', validate);
  input.addEventListener('change', validate);
  input.form?.addEventListener('submit', validate);
});

document.querySelectorAll('[data-association-source]').forEach((select) => {
  const form = select.closest('form');
  const nameInput = form?.querySelector('[data-association-name]');
  const urlInput = form?.querySelector('[data-association-url]');
  if (!form || !nameInput || !urlInput) return;

  select.addEventListener('change', () => {
    const option = select.options[select.selectedIndex];
    nameInput.value = option.dataset.name || '';
    urlInput.value = option.dataset.url || '';
    nameInput.readOnly = Boolean(option.dataset.name);
    urlInput.readOnly = Boolean(option.dataset.url);
  });
});

document.querySelectorAll('[data-bulk-form]').forEach((form) => {
  const boxes = [...document.querySelectorAll('[data-company-select]')];
  const selectAll = document.querySelector('[data-select-all]');
  const count = form.querySelector('[data-selected-count]');
  const actions = [...form.querySelectorAll('[data-bulk-action]')];
  const countTemplate = count?.textContent || '0';
  const update = () => {
    const selected = boxes.filter((box) => box.checked).length;
    if (count) count.textContent = countTemplate.replace(/\d+/, String(selected));
    actions.forEach((button) => { button.disabled = selected === 0; });
    if (selectAll) {
      selectAll.checked = boxes.length > 0 && selected === boxes.length;
      selectAll.indeterminate = selected > 0 && selected < boxes.length;
    }
  };
  selectAll?.addEventListener('change', () => {
    boxes.forEach((box) => { box.checked = selectAll.checked; });
    update();
  });
  boxes.forEach((box) => box.addEventListener('change', update));
  actions.forEach((button) => button.addEventListener('click', (event) => {
    if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) event.preventDefault();
  }));
  update();
});

document.querySelectorAll('[data-discovery-tabs]').forEach((tabs) => {
  const buttons = [...tabs.querySelectorAll('[data-discovery-tab]')];
  const panes = [...document.querySelectorAll('[data-discovery-pane]')];
  const available = new Set(buttons.map((button) => button.dataset.discoveryTab));
  const activate = (name, remember = true) => {
    const selected = available.has(name) ? name : 'keyword';
    buttons.forEach((button) => {
      const active = button.dataset.discoveryTab === selected;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
    });
    panes.forEach((pane) => { pane.hidden = pane.dataset.discoveryPane !== selected; });
    if (remember) {
      try { window.localStorage.setItem('leadharbor.discoveryTab', selected); } catch (_) {}
    }
  };
  buttons.forEach((button) => button.addEventListener('click', () => activate(button.dataset.discoveryTab)));
  tabs.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const current = buttons.findIndex((button) => button.classList.contains('active'));
    const offset = event.key === 'ArrowRight' ? 1 : -1;
    const next = buttons[(current + offset + buttons.length) % buttons.length];
    activate(next.dataset.discoveryTab);
    next.focus();
  });
  let initial = 'keyword';
  try { initial = window.localStorage.getItem('leadharbor.discoveryTab') || initial; } catch (_) {}
  activate(initial, false);
});

document.querySelectorAll('[data-scoring-form]').forEach((form) => {
  const inputs = [...form.querySelectorAll('[data-scoring-weight]')];
  const output = form.querySelector('[data-scoring-total]');
  const totalRow = form.querySelector('[data-scoring-total-row]');
  const warning = form.querySelector('[data-scoring-warning]');
  const save = form.querySelector('[data-scoring-save]');
  const required = Number(form.dataset.requiredTotal || 120);
  const update = () => {
    const total = inputs.reduce((sum, input) => sum + (Number(input.value) || 0), 0);
    const valid = total === required;
    if (output) output.value = String(total);
    totalRow?.classList.toggle('invalid', !valid);
    if (warning) warning.hidden = valid;
    if (save) save.disabled = !valid;
  };
  inputs.forEach((input) => input.addEventListener('input', update));
  update();
});
