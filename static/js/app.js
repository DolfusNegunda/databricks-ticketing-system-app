/* ==========================================================================
   Nexus Support -- frontend
   Talks only to the JSON API in support_app/routes.py. All DOM is built with
   createElement/textContent, so ticket and message text is never interpreted
   as markup.
   ========================================================================== */
(() => {
  'use strict';

  const API = '/api';
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const DELETE_WORD = 'DELETE';

  const state = {
    meta: null,
    tickets: [],
    total: 0,
    stats: null,
    selectedId: null,
    detail: null,
    filters: { statuses: [], priority: '', category: '', q: '', sort: 'activity' },
  };

  const $ = (id) => document.getElementById(id);

  // ----------------------------------------------------------------- utils --
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'icon');
    const use = document.createElementNS(SVG_NS, 'use');
    use.setAttribute('href', `#${name}`);
    svg.append(use);
    return svg;
  }

  const titleCase = (value) =>
    String(value || '')
      .split('_')
      .filter(Boolean)
      .map((part) => part[0].toUpperCase() + part.slice(1))
      .join(' ');

  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  const UNITS = [
    ['year', 31536000],
    ['month', 2592000],
    ['week', 604800],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ];

  function relTime(iso) {
    if (!iso) return '--';
    const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
    const magnitude = Math.abs(seconds);
    if (magnitude < 45) return 'just now';
    for (const [unit, size] of UNITS) {
      if (magnitude >= size) return rtf.format(Math.round(seconds / size), unit);
    }
    return rtf.format(Math.round(seconds), 'second');
  }

  function absTime(iso) {
    if (!iso) return '--';
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  }

  function debounce(fn, wait) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  // ------------------------------------------------------------- transport --
  class ApiFailure extends Error {
    constructor(message, { code = 'error', fields = {}, status = 0, payload = null } = {}) {
      super(message);
      this.code = code;
      this.fields = fields;
      this.status = status;
      // Full response body. /api/health returns a diagnostic payload even when
      // it fails, and the root cause lives in there rather than in `message`.
      this.payload = payload;
    }
  }

  async function request(path, { method = 'GET', body, params } = {}) {
    let url = `${API}${path}`;
    if (params) {
      const search = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (Array.isArray(value)) value.forEach((v) => v && search.append(key, v));
        else if (value !== '' && value !== null && value !== undefined) {
          search.set(key, value);
        }
      }
      const query = search.toString();
      if (query) url += `?${query}`;
    }

    let response;
    try {
      response = await fetch(url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (networkError) {
      throw new ApiFailure(
        'Could not reach the app server. Check your connection and retry.',
        { code: 'network_error' },
      );
    }

    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = null;
      }
    }

    if (!response.ok) {
      const err = payload && payload.error ? payload.error : {};
      throw new ApiFailure(err.message || `Request failed (${response.status}).`, {
        code: err.code,
        fields: err.fields || {},
        status: response.status,
        payload,
      });
    }
    return payload;
  }

  // ---------------------------------------------------------------- toasts --
  function toast(kind, title, message) {
    const node = el(
      'div',
      { class: `toast toast--${kind}`, role: 'status' },
      icon(kind === 'error' ? 'i-alert' : 'i-check'),
      el(
        'div',
        { class: 'toast__text' },
        el('strong', { text: title }),
        message ? el('span', { text: message }) : null,
      ),
    );
    $('toasts').append(node);
    setTimeout(() => {
      node.classList.add('is-leaving');
      node.addEventListener('animationend', () => node.remove(), { once: true });
    }, kind === 'error' ? 7000 : 3600);
  }

  const reportError = (error, fallbackTitle = 'Something went wrong') => {
    console.error(error);
    toast('error', fallbackTitle, error instanceof Error ? error.message : String(error));
  };

  function busy(button, isBusy) {
    if (!button) return;
    button.classList.toggle('is-busy', isBusy);
    button.disabled = isBusy;
  }

  // ------------------------------------------------------------- form state -
  function clearFieldErrors(form) {
    form.querySelectorAll('.field.is-invalid').forEach((f) => f.classList.remove('is-invalid'));
    form.querySelectorAll('[data-error-for]').forEach((span) => {
      span.hidden = true;
      span.textContent = '';
    });
    const summary = form.querySelector('.form-error');
    if (summary) {
      summary.hidden = true;
      summary.textContent = '';
    }
  }

  function showFieldErrors(form, failure) {
    clearFieldErrors(form);
    const fields = failure.fields || {};
    let matched = 0;
    for (const [field, message] of Object.entries(fields)) {
      const span = form.querySelector(`[data-error-for="${field}"]`);
      if (!span) continue;
      span.textContent = message;
      span.hidden = false;
      span.closest('.field')?.classList.add('is-invalid');
      matched += 1;
    }
    const summary = form.querySelector('.form-error');
    if (summary && (!matched || failure.message)) {
      summary.textContent = failure.message;
      summary.hidden = false;
    }
    return matched;
  }

  // ------------------------------------------------------------------ meta --
  function fillSelect(select, values, { includeAny = false, anyLabel = 'Any' } = {}) {
    select.replaceChildren();
    if (includeAny) select.append(el('option', { value: '', text: anyLabel }));
    values.forEach((value) =>
      select.append(el('option', { value, text: titleCase(value) })),
    );
  }

  function applyMeta(meta) {
    state.meta = meta;
    state.filters.sort = meta.default_sort || 'activity';

    fillSelect($('priority-filter'), meta.priorities, { includeAny: true });
    fillSelect($('category-filter'), meta.categories, { includeAny: true });
    fillSelect($('sort-select'), meta.sort_options);
    $('sort-select').value = state.filters.sort;

    fillSelect($('new-status'), meta.statuses);
    fillSelect($('new-priority'), meta.priorities);
    fillSelect($('new-category'), meta.categories);
    $('new-status').value = 'open';
    $('new-priority').value = 'medium';
    $('new-category').value = 'general';
    $('new-created-by').value = meta.current_user || '';

    fillSelect($('status-control'), meta.statuses);
    fillSelect($('priority-control'), meta.priorities);
    fillSelect($('category-control'), meta.categories);
    fillSelect($('role-select'), meta.author_roles);
    $('role-select').value = 'agent';

    $('message-input').maxLength = meta.limits.message_max;
    $('new-title').maxLength = meta.limits.title_max;
    $('confirm-word').textContent = DELETE_WORD;

    renderStatusFilters();
  }

  function renderStatusFilters() {
    const host = $('status-filters');
    host.replaceChildren();
    const counts = state.stats?.by_status || {};

    const allActive = state.filters.statuses.length === 0;
    host.append(
      el('button', {
        type: 'button',
        class: 'filter-chip',
        'aria-pressed': String(allActive),
        onclick: () => {
          state.filters.statuses = [];
          renderStatusFilters();
          loadTickets();
        },
      }, 'All', el('span', { class: 'tally', text: String(state.stats?.total ?? '') })),
    );

    (state.meta?.statuses || []).forEach((status) => {
      const active = state.filters.statuses.includes(status);
      host.append(
        el('button', {
          type: 'button',
          class: 'filter-chip',
          dataset: { status },
          'aria-pressed': String(active),
          onclick: () => toggleStatus(status),
        },
        el('span', { class: 'swatch' }),
        titleCase(status),
        el('span', { class: 'tally', text: String(counts[status] ?? '') }),
        ),
      );
    });
  }

  function toggleStatus(status) {
    const set = new Set(state.filters.statuses);
    if (set.has(status)) set.delete(status);
    else set.add(status);
    state.filters.statuses = [...set];
    renderStatusFilters();
    loadTickets();
  }

  function filtersActive() {
    const f = state.filters;
    return Boolean(f.statuses.length || f.priority || f.category || f.q);
  }

  // ----------------------------------------------------------------- stats --
  function renderStats() {
    const stats = state.stats;
    if (!stats) return;
    const values = {
      total: stats.total,
      open: stats.by_status.open,
      in_progress: stats.by_status.in_progress,
      resolved: stats.by_status.resolved,
      needs_attention: stats.needs_attention,
      avg_resolution_hours:
        stats.avg_resolution_hours === null ? '--' : `${stats.avg_resolution_hours}h`,
    };
    for (const [key, value] of Object.entries(values)) {
      const cell = document.querySelector(`.stat[data-stat="${key}"] .stat__value`);
      if (cell) cell.textContent = String(value);
    }
  }

  // ------------------------------------------------------------ ticket list -
  function ticketCard(ticket) {
    const selected = ticket.ticket_id === state.selectedId;
    return el(
      'li',
      {},
      el(
        'button',
        {
          type: 'button',
          class: `ticket${selected ? ' is-selected' : ''}`,
          dataset: { priority: ticket.priority, status: ticket.status },
          'aria-current': selected ? 'true' : null,
          onclick: () => selectTicket(ticket.ticket_id),
        },
        el(
          'div',
          { class: 'ticket__top' },
          el('span', { class: 'ticket-id', text: `#${ticket.ticket_id}` }),
          el('span', {
            class: 'pill pill--status',
            dataset: { status: ticket.status },
            text: titleCase(ticket.status),
          }),
          el('span', {
            class: 'pill pill--priority',
            dataset: { priority: ticket.priority },
            text: titleCase(ticket.priority),
          }),
        ),
        el('span', { class: 'ticket__title', text: ticket.title }),
        el(
          'div',
          { class: 'ticket__foot' },
          el('span', { class: 'who', title: ticket.created_by, text: ticket.created_by }),
          el('span', { class: 'sep', text: '|' }),
          el('span', {
            text: `${ticket.message_count} message${ticket.message_count === 1 ? '' : 's'}`,
          }),
          el('span', { class: 'sep', text: '|' }),
          el('span', {
            title: absTime(ticket.last_activity_at),
            text: relTime(ticket.last_activity_at),
          }),
        ),
      ),
    );
  }

  function renderList() {
    const list = $('ticket-list');
    const empty = $('list-empty');
    list.replaceChildren();

    if (!state.tickets.length) {
      empty.hidden = false;
      $('result-count').textContent = '';
      return;
    }
    empty.hidden = true;
    state.tickets.forEach((ticket) => list.append(ticketCard(ticket)));

    const shown = state.tickets.length;
    $('result-count').textContent =
      shown === state.total
        ? `${shown} ticket${shown === 1 ? '' : 's'}`
        : `Showing ${shown} of ${state.total} tickets`;
    $('clear-filters').hidden = !filtersActive();
  }

  function showSkeletons() {
    const list = $('ticket-list');
    $('list-empty').hidden = true;
    list.replaceChildren(
      ...Array.from({ length: 5 }, () => el('li', {}, el('div', { class: 'skeleton' }))),
    );
  }

  // ---------------------------------------------------------------- detail --
  function metaEntry(label, value, mono = false) {
    return el(
      'div',
      {},
      el('dt', { text: label }),
      el('dd', { class: mono ? 'mono' : null, title: value, text: value }),
    );
  }

  function statusTrail(history) {
    if (!history || !history.length) return null;
    const ordered = [...history].reverse();
    const trail = [];
    ordered.forEach((entry, index) => {
      if (index === 0 && entry.from_status) trail.push(titleCase(entry.from_status));
      trail.push(titleCase(entry.to_status));
    });
    return metaEntry('Status trail', trail.join(' -> '));
  }

  function messageItem(message, isNew = false) {
    return el(
      'li',
      {
        class: `message${isNew ? ' is-new' : ''}`,
        dataset: { role: message.author_role },
      },
      el(
        'div',
        { class: 'message__head' },
        el('span', { class: 'message__author', text: message.author }),
        el('span', { class: 'message__role', text: message.author_role }),
        el('span', {
          title: absTime(message.created_at),
          text: relTime(message.created_at),
        }),
      ),
      el('div', { class: 'message__text', text: message.message_text }),
    );
  }

  function renderDetail(newMessageId = null) {
    const ticket = state.detail;
    const panel = $('detail');
    const placeholder = $('detail-placeholder');

    if (!ticket) {
      panel.hidden = true;
      placeholder.hidden = false;
      return;
    }
    placeholder.hidden = true;
    panel.hidden = false;

    $('detail-id').textContent = `Ticket #${ticket.ticket_id}`;
    $('detail-title').textContent = ticket.title;
    const description = $('detail-description');
    description.textContent = ticket.description || '';
    description.hidden = !ticket.description;

    $('detail-pills').replaceChildren(
      el('span', {
        class: 'pill pill--status',
        dataset: { status: ticket.status },
        text: titleCase(ticket.status),
      }),
      el('span', {
        class: 'pill pill--priority',
        dataset: { priority: ticket.priority },
        text: `${titleCase(ticket.priority)} priority`,
      }),
      el('span', { class: 'pill pill--category', text: titleCase(ticket.category) }),
    );

    $('status-control').value = ticket.status;
    $('priority-control').value = ticket.priority;
    $('category-control').value = ticket.category;

    $('detail-meta').replaceChildren(
      metaEntry('Reported by', ticket.created_by, true),
      metaEntry('Assigned to', ticket.assigned_to || 'Unassigned', Boolean(ticket.assigned_to)),
      metaEntry('Created', absTime(ticket.created_at)),
      metaEntry('Last updated', `${relTime(ticket.updated_at)} (${absTime(ticket.updated_at)})`),
      ticket.resolved_at ? metaEntry('Resolved', absTime(ticket.resolved_at)) : null,
      statusTrail(ticket.status_history),
    );

    const messages = ticket.messages || [];
    $('message-count').textContent = `(${messages.length})`;
    $('messages').replaceChildren(
      ...(messages.length
        ? messages.map((m) => messageItem(m, m.message_id === newMessageId))
        : [el('li', { class: 'empty__hint', text: 'No messages on this ticket yet.' })]),
    );
  }

  // ----------------------------------------------------------------- loads --
  async function loadStats() {
    try {
      state.stats = await request('/stats');
      renderStats();
      renderStatusFilters();
    } catch (error) {
      console.warn('Stats unavailable', error);
    }
  }

  async function loadTickets({ silent = false } = {}) {
    if (!silent) showSkeletons();
    try {
      const data = await request('/tickets', {
        params: {
          status: state.filters.statuses,
          priority: state.filters.priority,
          category: state.filters.category,
          q: state.filters.q,
          sort: state.filters.sort,
          limit: 200,
        },
      });
      state.tickets = data.items;
      state.total = data.total;
      renderList();
    } catch (error) {
      state.tickets = [];
      renderList();
      reportError(error, 'Could not load tickets');
    }
  }

  async function selectTicket(ticketId, { silent = false } = {}) {
    state.selectedId = ticketId;
    renderList();
    document.body.classList.add('detail-open');
    try {
      state.detail = await request(`/tickets/${ticketId}`);
      renderDetail();
      if (!silent) $('detail').scrollTop = 0;
    } catch (error) {
      state.detail = null;
      renderDetail();
      reportError(error, 'Could not open that ticket');
      if (error.status === 404) {
        state.selectedId = null;
        document.body.classList.remove('detail-open');
        await refreshAll();
      }
    }
  }

  async function refreshAll({ keepSelection = true } = {}) {
    await Promise.all([loadStats(), loadTickets({ silent: true })]);
    if (keepSelection && state.selectedId) {
      await selectTicket(state.selectedId, { silent: true });
    }
  }

  async function checkHealth() {
    const dot = $('health-dot');
    const label = $('health-label');
    const banner = $('banner');
    try {
      const health = await request('/health');
      dot.className = 'dot dot--ok';
      label.textContent = 'Lakebase connected';
      banner.hidden = true;
      const lb = health.lakebase || {};
      $('footer-target').textContent =
        `${lb.database || 'databricks_postgres'}.${lb.schema || 'support'} ` +
        `on ${lb.instance || lb.host || 'lakebase'} (${lb.auth_mode})`;
    } catch (error) {
      dot.className = 'dot dot--bad';
      banner.hidden = false;

      // If we reached the database but the schema was never applied, the
      // startup bootstrap error is the root cause -- "relation does not exist"
      // is only the symptom, and reporting it sends people down the wrong path.
      const bootstrap = error.payload?.bootstrap;
      const connected = Boolean(error.payload?.lakebase?.host);

      if (bootstrap && bootstrap.attempted && bootstrap.ok === false && bootstrap.error) {
        label.textContent = 'Schema not created';
        $('banner-title').textContent =
          'Connected to Lakebase, but the tables were never created';
        $('banner-detail').textContent =
          `${bootstrap.error} — retry with POST /api/admin/bootstrap?seed=true`;
      } else {
        label.textContent = 'Lakebase unavailable';
        $('banner-title').textContent = connected
          ? 'Lakebase reachable, but the request failed'
          : 'Lakebase is unreachable';
        $('banner-detail').textContent = error.message;
      }

      const lb = error.payload?.lakebase;
      $('footer-target').textContent = lb?.host
        ? `${lb.database}.${lb.schema} on ${lb.host} (${lb.auth_mode})`
        : 'no Lakebase connection';
    }
  }

  // ---------------------------------------------------------------- modals --
  let lastFocused = null;

  function openModal(id, focusId) {
    lastFocused = document.activeElement;
    $(id).hidden = false;
    document.body.style.overflow = 'hidden';
    if (focusId) setTimeout(() => $(focusId)?.focus(), 40);
  }

  function closeModal(id) {
    $(id).hidden = true;
    document.body.style.overflow = '';
    if (lastFocused instanceof HTMLElement) lastFocused.focus();
  }

  function openNewTicket() {
    const form = $('new-form');
    form.reset();
    clearFieldErrors(form);
    $('new-status').value = 'open';
    $('new-priority').value = 'medium';
    $('new-category').value = 'general';
    $('new-created-by').value = state.meta?.current_user || '';
    openModal('new-modal', 'new-title');
  }

  // --------------------------------------------------------------- actions --
  async function createTicket(event) {
    event.preventDefault();
    const form = $('new-form');
    const button = $('create-btn');
    clearFieldErrors(form);

    const payload = {
      title: $('new-title').value,
      description: $('new-description').value || null,
      status: $('new-status').value,
      priority: $('new-priority').value,
      category: $('new-category').value,
      created_by: $('new-created-by').value || null,
      assigned_to: $('new-assigned-to').value || null,
      first_message: $('new-message').value || null,
      author_role: 'customer',
    };

    busy(button, true);
    try {
      const ticket = await request('/tickets', { method: 'POST', body: payload });
      closeModal('new-modal');
      toast('ok', `Ticket #${ticket.ticket_id} created`, 'Saved to Lakebase.');
      await refreshAll({ keepSelection: false });
      await selectTicket(ticket.ticket_id);
    } catch (error) {
      if (error instanceof ApiFailure) showFieldErrors(form, error);
      else reportError(error, 'Could not create the ticket');
    } finally {
      busy(button, false);
    }
  }

  async function patchTicket(changes, description) {
    if (!state.selectedId) return;
    try {
      state.detail = await request(`/tickets/${state.selectedId}`, {
        method: 'PATCH',
        body: changes,
      });
      renderDetail();
      toast('ok', description, `Ticket #${state.selectedId} updated in Lakebase.`);
      await Promise.all([loadStats(), loadTickets({ silent: true })]);
    } catch (error) {
      reportError(error, 'Could not update the ticket');
      await selectTicket(state.selectedId, { silent: true });
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    if (!state.selectedId) return;

    const input = $('message-input');
    const errorSpan = $('message-error');
    const field = input.closest('.field');
    const text = input.value.trim();

    errorSpan.hidden = true;
    field.classList.remove('is-invalid');

    if (!text) {
      errorSpan.textContent = 'Write something before sending.';
      errorSpan.hidden = false;
      field.classList.add('is-invalid');
      input.focus();
      return;
    }

    const button = $('send-btn');
    busy(button, true);
    try {
      const message = await request(`/tickets/${state.selectedId}/messages`, {
        method: 'POST',
        body: { message_text: text, author_role: $('role-select').value },
      });
      input.value = '';
      updateCounter();
      state.detail = await request(`/tickets/${state.selectedId}`);
      renderDetail(message.message_id);
      $('messages').lastElementChild?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      toast('ok', 'Message added', 'Stored in Lakebase.');
      await loadTickets({ silent: true });
    } catch (error) {
      if (error instanceof ApiFailure && error.fields.message_text) {
        errorSpan.textContent = error.fields.message_text;
        errorSpan.hidden = false;
        field.classList.add('is-invalid');
      } else reportError(error, 'Could not add the message');
    } finally {
      busy(button, false);
    }
  }

  function openDeleteConfirm() {
    if (!state.detail) return;
    const ticket = state.detail;
    $('confirm-lead').replaceChildren(
      document.createTextNode('You are about to delete '),
      el('strong', { text: `#${ticket.ticket_id} -- ${ticket.title}` }),
      document.createTextNode(
        ` and its ${ticket.messages?.length ?? 0} message(s).`,
      ),
    );
    $('confirm-input').value = '';
    $('confirm-delete-btn').disabled = true;
    openModal('confirm-modal', 'confirm-input');
  }

  async function confirmDelete() {
    const ticketId = state.selectedId;
    if (!ticketId || $('confirm-input').value.trim().toUpperCase() !== DELETE_WORD) return;

    const button = $('confirm-delete-btn');
    busy(button, true);
    try {
      const result = await request(`/tickets/${ticketId}`, { method: 'DELETE' });
      closeModal('confirm-modal');
      state.selectedId = null;
      state.detail = null;
      document.body.classList.remove('detail-open');
      renderDetail();
      toast(
        'ok',
        `Ticket #${ticketId} deleted`,
        `Removed with ${result.deleted.deleted_messages} message(s).`,
      );
      await refreshAll({ keepSelection: false });
    } catch (error) {
      reportError(error, 'Could not delete the ticket');
    } finally {
      busy(button, false);
    }
  }

  function updateCounter() {
    const input = $('message-input');
    const max = input.maxLength > 0 ? input.maxLength : 5000;
    const counter = $('message-counter');
    counter.textContent = `${input.value.length} / ${max}`;
    counter.classList.toggle('is-near', input.value.length > max * 0.9);
  }

  function clearFilters() {
    state.filters.statuses = [];
    state.filters.priority = '';
    state.filters.category = '';
    state.filters.q = '';
    $('search-input').value = '';
    $('priority-filter').value = '';
    $('category-filter').value = '';
    renderStatusFilters();
    loadTickets();
  }

  // ------------------------------------------------------------------ wire --
  function wire() {
    $('new-ticket-btn').addEventListener('click', openNewTicket);
    $('new-form').addEventListener('submit', createTicket);
    $('composer').addEventListener('submit', sendMessage);
    $('delete-btn').addEventListener('click', openDeleteConfirm);
    $('confirm-delete-btn').addEventListener('click', confirmDelete);
    $('refresh-btn').addEventListener('click', async () => {
      busy($('refresh-btn'), true);
      await Promise.all([checkHealth(), refreshAll()]);
      busy($('refresh-btn'), false);
      toast('ok', 'Reloaded from Lakebase');
    });
    $('health-chip').addEventListener('click', checkHealth);
    $('banner-retry').addEventListener('click', async () => {
      const button = $('banner-retry');
      busy(button, true);
      try {
        // Re-applying the schema is idempotent and is the fix for the common
        // case (bootstrap ran before the credential was available), so retry
        // means retry the thing that failed -- not just re-check.
        const result = await request('/admin/bootstrap?seed=true', { method: 'POST' });
        toast('ok', 'Schema applied', result.seeded ? 'Demo tickets loaded.' : 'Tables are ready.');
      } catch (error) {
        reportError(error, 'Could not apply the schema');
      } finally {
        busy(button, false);
      }
      await checkHealth();
      await refreshAll({ keepSelection: false });
    });
    $('clear-filters').addEventListener('click', clearFilters);
    $('detail-back').addEventListener('click', () => {
      document.body.classList.remove('detail-open');
    });

    $('search-input').addEventListener(
      'input',
      debounce((event) => {
        state.filters.q = event.target.value.trim();
        loadTickets({ silent: true });
      }, 260),
    );

    $('priority-filter').addEventListener('change', (event) => {
      state.filters.priority = event.target.value;
      loadTickets();
    });
    $('category-filter').addEventListener('change', (event) => {
      state.filters.category = event.target.value;
      loadTickets();
    });
    $('sort-select').addEventListener('change', (event) => {
      state.filters.sort = event.target.value;
      loadTickets();
    });

    $('status-control').addEventListener('change', (event) =>
      patchTicket({ status: event.target.value }, 'Status updated'),
    );
    $('priority-control').addEventListener('change', (event) =>
      patchTicket({ priority: event.target.value }, 'Priority updated'),
    );
    $('category-control').addEventListener('change', (event) =>
      patchTicket({ category: event.target.value }, 'Category updated'),
    );

    $('message-input').addEventListener('input', updateCounter);
    $('message-input').addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        $('composer').requestSubmit();
      }
    });

    $('confirm-input').addEventListener('input', (event) => {
      $('confirm-delete-btn').disabled =
        event.target.value.trim().toUpperCase() !== DELETE_WORD;
    });
    $('confirm-input').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !$('confirm-delete-btn').disabled) {
        event.preventDefault();
        confirmDelete();
      }
    });

    document.querySelectorAll('[data-close-modal]').forEach((node) =>
      node.addEventListener('click', () => {
        const modal = node.closest('.modal');
        if (modal) closeModal(modal.id);
      }),
    );

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const open = [...document.querySelectorAll('.modal')].find((m) => !m.hidden);
      if (open) closeModal(open.id);
    });
  }

  // ------------------------------------------------------------------ boot --
  async function boot() {
    wire();
    updateCounter();
    showSkeletons();

    try {
      applyMeta(await request('/meta'));
    } catch (error) {
      reportError(error, 'Could not load app configuration');
    }

    await checkHealth();
    await loadStats();
    await loadTickets();

    // Deep link support: /#ticket-12
    const match = /^#ticket-(\d+)$/.exec(window.location.hash);
    if (match) await selectTicket(Number(match[1]));
    else document.body.classList.remove('detail-open');
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
