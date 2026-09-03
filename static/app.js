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

document.querySelectorAll('[data-hubspot-settings]').forEach((card) => {
  const button = card.querySelector('[data-hubspot-test]');
  const status = card.querySelector('[data-hubspot-connection-status]');
  const result = card.querySelector('[data-hubspot-test-result]');
  if (!button || !status || !result) return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    result.textContent = '…';
    try {
      const response = await fetch(card.dataset.testUrl, {
        method: 'POST',
        headers: { 'X-CSRF-Token': card.dataset.csrf },
      });
      const payload = await response.json();
      result.textContent = payload.message || payload.category || 'Connection error';
      status.textContent = payload.status === 'connected' ? payload.message : card.dataset.errorLabel;
      status.classList.toggle('configured', payload.status === 'connected');
      status.classList.toggle('connection-error', payload.status !== 'connected');
    } catch (_) {
      result.textContent = card.dataset.networkLabel;
      status.classList.add('connection-error');
    } finally {
      button.disabled = false;
    }
  });
});

document.querySelectorAll('[data-hubspot-preview]').forEach((dialog) => {
  const checkButton = document.querySelector('[data-hubspot-check]');
  const resultsContainer = dialog.querySelector('[data-hubspot-results]');
  const summaryContainer = dialog.querySelector('[data-hubspot-summary]');
  const progress = dialog.querySelector('[data-hubspot-progress]');
  const syncButton = dialog.querySelector('[data-hubspot-sync]');
  const labels = {
    NEW: dialog.dataset.labelNew,
    DUPLICATE: dialog.dataset.labelDuplicate,
    ENRICHABLE: dialog.dataset.labelEnrichable,
    CONFLICT: dialog.dataset.labelConflict,
    FAILED: dialog.dataset.labelFailed,
    SYNCED: dialog.dataset.labelSynced,
    RECHECK_REQUIRED: dialog.dataset.labelRecheckRequired,
    SKIPPED: dialog.dataset.labelSkipped,
  };
  let current = null;

  const openDialog = () => {
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  };
  const closeDialog = () => {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  };
  dialog.querySelectorAll('[data-hubspot-close]').forEach((button) => button.addEventListener('click', closeDialog));

  const appendText = (parent, tag, text, className = '') => {
    const node = document.createElement(tag);
    node.textContent = text || '—';
    if (className) node.className = className;
    parent.appendChild(node);
    return node;
  };

  const renderDifferences = (card, differences, objectType) => {
    if (!differences?.length) return;
    const table = document.createElement('div');
    table.className = 'hubspot-difference-table';
    differences.forEach((difference) => {
      const row = document.createElement('div');
      row.className = `hubspot-difference ${difference.action.toLowerCase()}`;
      appendText(row, 'strong', difference.field);
      appendText(row, 'span', `${dialog.dataset.labelHubspot}: ${difference.hubspot_value || '—'}`);
      appendText(row, 'span', `${dialog.dataset.labelLocal}: ${difference.leadharbor_value || '—'}`);
      if (difference.action === 'CONFLICT') {
        const select = document.createElement('select');
        select.dataset.overwriteObject = objectType;
        select.dataset.overwriteField = difference.field;
        const keep = document.createElement('option');
        keep.value = 'keep';
        keep.textContent = dialog.dataset.labelKeep;
        const overwrite = document.createElement('option');
        overwrite.value = 'overwrite';
        overwrite.textContent = dialog.dataset.labelOverwrite;
        select.append(keep, overwrite);
        row.appendChild(select);
      } else {
        appendText(row, 'em', difference.action === 'FILL_MISSING' ? dialog.dataset.labelFill : dialog.dataset.labelNoChange);
      }
      table.appendChild(row);
    });
    card.appendChild(table);
  };

  const render = (payload) => {
    current = payload;
    resultsContainer.replaceChildren();
    summaryContainer.replaceChildren();
    ['new', 'duplicate', 'enrichable', 'conflict', 'failed'].forEach((key) => {
      const item = document.createElement('span');
      appendText(item, 'strong', String(payload.summary[key] || 0));
      appendText(item, 'small', labels[key.toUpperCase()]);
      summaryContainer.appendChild(item);
    });
    payload.results.forEach((item) => {
      const card = document.createElement('article');
      card.className = `hubspot-preview-card ${item.status.toLowerCase()}`;
      card.dataset.companyId = String(item.company_id);
      const heading = document.createElement('header');
      appendText(heading, 'h3', item.company_name);
      appendText(heading, 'span', labels[item.status] || item.status, 'hubspot-status');
      card.appendChild(heading);
      if (item.match_reason) appendText(card, 'p', `${item.match_reason} · ${item.match_confidence}`);
      if (item.error) appendText(card, 'p', item.error, 'hubspot-error');
      if (item.status === 'NEW') {
        const fields = Object.entries(item.company_properties || {}).map(([key, value]) => `${key}: ${value}`);
        appendText(card, 'p', fields.join(' · '), 'hubspot-new-fields');
      }
      renderDifferences(card, item.company_differences, 'company');
      renderDifferences(card, item.contact_differences, 'contact');
      resultsContainer.appendChild(card);
      const rowStatus = document.querySelector(`[data-company-hubspot-status="${item.company_id}"]`);
      if (rowStatus) {
        rowStatus.textContent = labels[item.status] || item.status;
        rowStatus.className = `hubspot-status ${item.status.toLowerCase()}`;
      }
    });
    syncButton.disabled = !payload.results.some((item) => ['NEW', 'ENRICHABLE'].includes(item.status) || (item.status === 'CONFLICT' && item.match_confidence === 'exact'));
  };

  checkButton?.addEventListener('click', async () => {
    const ids = [...document.querySelectorAll('[data-company-select]:checked')].map((box) => Number(box.value));
    if (!ids.length) return;
    openDialog();
    progress.textContent = dialog.dataset.labelChecking;
    resultsContainer.replaceChildren();
    summaryContainer.replaceChildren();
    syncButton.disabled = true;
    checkButton.disabled = true;
    try {
      const response = await fetch(checkButton.dataset.checkUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': dialog.dataset.csrf },
        body: JSON.stringify({ company_ids: ids }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'HubSpot check failed');
      progress.textContent = '';
      render(payload);
    } catch (error) {
      progress.textContent = error.message;
    } finally {
      checkButton.disabled = false;
    }
  });

  const approvalFor = (item) => {
    const card = resultsContainer.querySelector(`[data-company-id="${item.company_id}"]`);
    const companyOverwrite = [...(card?.querySelectorAll('[data-overwrite-object="company"]') || [])]
      .filter((select) => select.value === 'overwrite').map((select) => select.dataset.overwriteField);
    const contactOverwrite = [...(card?.querySelectorAll('[data-overwrite-object="contact"]') || [])]
      .filter((select) => select.value === 'overwrite').map((select) => select.dataset.overwriteField);
    if (item.status === 'CONFLICT' && item.match_confidence !== 'exact') return null;
    if (['DUPLICATE', 'FAILED'].includes(item.status)) return null;
    const actions = [];
    if (item.hubspot_company_id) {
      if (item.company_differences?.some((difference) => difference.action === 'FILL_MISSING') || companyOverwrite.length) actions.push('ENRICH_COMPANY');
    } else actions.push('CREATE_COMPANY');
    const hasContact = Object.keys(item.contact_properties || {}).length > 0;
    if (hasContact) {
      if (item.hubspot_contact_id) {
        if (item.contact_differences?.some((difference) => difference.action === 'FILL_MISSING') || contactOverwrite.length) actions.push('ENRICH_CONTACT');
      } else actions.push('CREATE_CONTACT');
      actions.push('ASSOCIATE_CONTACT_COMPANY');
    }
    if (!actions.length) return null;
    return {
      company_id: item.company_id,
      actions,
      company_overwrite_fields: companyOverwrite,
      contact_overwrite_fields: contactOverwrite,
    };
  };

  syncButton?.addEventListener('click', async () => {
    const approvals = (current?.results || []).map(approvalFor).filter(Boolean);
    if (!approvals.length) {
      progress.textContent = dialog.dataset.labelNoActions;
      return;
    }
    syncButton.disabled = true;
    progress.textContent = dialog.dataset.labelSyncing;
    try {
      const response = await fetch(checkButton.dataset.syncUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': dialog.dataset.csrf },
        body: JSON.stringify({ batch_id: current.batch_id, approvals }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || 'HubSpot sync failed');
      progress.textContent = `${dialog.dataset.labelSyncComplete} ${payload.summary.success}/${payload.results.length}`;
      payload.results.forEach((item) => {
        const rowStatus = document.querySelector(`[data-company-hubspot-status="${item.company_id}"]`);
        if (rowStatus) {
          rowStatus.textContent = labels[item.status] || item.status;
          rowStatus.className = `hubspot-status ${item.status.toLowerCase()}`;
        }
      });
    } catch (error) {
      progress.textContent = error.message;
      syncButton.disabled = false;
    }
  });
});
