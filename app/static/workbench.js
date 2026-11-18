const $ = (selector) => document.querySelector(selector);
const root = $("#main");
const notice = $("#notice");
const state = {
  campaigns: [], nextOffset: null, selectedId: null, campaign: null, report: null,
  days: 7, view: "home", navigation: 0, reportRequest: 0, offers: null, catalog: null,
};
const numbers = new Intl.NumberFormat("ru-RU");
const number = (value) => numbers.format(value);
const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);

async function api(path, body, method = "POST") {
  const options = body === undefined ? {} : {
    method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  };
  const response = await fetch(`/api${path}`, options);
  const data = await response.json();
  if (response.ok) return data;
  const detail = data.detail;
  const validation = Array.isArray(detail) ? detail.find((error) => error.type === "value_error") : null;
  const message = typeof detail === "string" ? detail : validation?.msg.replace(/^Value error, /, "");
  throw new Error(message || `Проверьте параметры запроса (HTTP ${response.status}).`);
}

function inform(message, warning = false) {
  notice.textContent = message;
  notice.classList.toggle("error", warning);
  notice.hidden = !message;
}

function navigation(view, campaignId = null) {
  state.view = view;
  state.selectedId = campaignId;
  state.campaign = null;
  state.report = null;
  state.reportError = "";
  state.navigation += 1;
  inform("");
  const url = new URL(location.href);
  if (campaignId) url.searchParams.set("campaign", campaignId);
  else url.searchParams.delete("campaign");
  history.replaceState(null, "", url);
  root.setAttribute("aria-busy", "true");
  renderList();
  return state.navigation;
}

function renderList() {
  $("#campaign-list").innerHTML = state.campaigns.map((item) => `
    <button type="button" class="campaign-link ${state.selectedId === item.id ? "selected" : ""}"
        ${state.selectedId === item.id ? 'aria-current="page"' : ""} data-campaign="${item.id}">
      <span class="campaign-name">${safe(item.name)}</span>
      <span class="campaign-id">#${item.id}</span>
    </button>`).join("");
  $("#more-campaigns").hidden = state.nextOffset === null;
}

async function listCampaigns(more = false) {
  const button = more ? $("#more-campaigns") : $("#reload-list");
  button.disabled = true;
  try {
    const page = await api(`/campaigns?offset=${more ? state.nextOffset : 0}`);
    const known = new Set(state.campaigns.map((item) => item.id));
    state.campaigns = more ? [...state.campaigns, ...page.items.filter((item) => !known.has(item.id))] : page.items;
    state.nextOffset = page.next_offset;
    $("#list-message").textContent = state.campaigns.length ? "" : "Доступных кампаний пока нет.";
    renderList();
  } catch (error) {
    $("#list-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function getOffers() {
  state.offers ??= await api("/offers");
  return state.offers;
}

function renderHome() {
  root.removeAttribute("aria-busy");
  root.innerHTML = `
    <section class="welcome panel">
      <h1>Кампании Keitaro</h1>
      <p>Выберите кампанию в списке или откройте её по ID, чтобы посмотреть потоки, статистику и состав офферов.</p>
      <button class="primary" type="button" data-action="create">Создать кампанию</button>
    </section>`;
}

async function openCampaign(id, refresh = false) {
  const nav = navigation("campaign", id);
  root.innerHTML = '<p class="loading">Загружаем кампанию…</p>';
  try {
    if (refresh) state.offers = null;
    const [campaign, catalog, offers] = await Promise.allSettled([
      api(`/campaigns/${id}`), api("/catalog"), getOffers(),
    ]);
    if (nav !== state.navigation) return;
    if (campaign.status === "rejected") throw campaign.reason;
    state.campaign = campaign.value;
    if (catalog.status === "fulfilled") state.catalog = catalog.value;
    if (offers.status === "rejected") inform("Справочник офферов недоступен. Можно использовать ID.", true);
    renderCampaign();
    await loadReport();
  } catch (error) {
    if (nav !== state.navigation) return;
    root.innerHTML = `<section class="panel error-panel"><h1>Кампания #${id}</h1>
      <p>${safe(error.message)}</p>
      <button class="secondary" type="button" data-action="refresh">Повторить</button></section>`;
  } finally {
    if (nav === state.navigation) root.removeAttribute("aria-busy");
  }
}

function catalogName(key, id) {
  return state.catalog?.[key]?.find((item) => item.id === Number(id))?.name || (id ? `#${id}` : "—");
}

function renderCampaign() {
  const campaign = state.campaign;
  const trackerLink = state.status.tracker_url
    ? `<a class="secondary" href="${safe(state.status.tracker_url)}/admin/#!/campaign/${campaign.id}/main"
          target="_blank" rel="noopener noreferrer">В Keitaro ↗</a>` : "";
  const status = {active: "Активна", disabled: "Отключена", deleted: "В архиве"};
  root.innerHTML = `
    <section class="campaign-header" aria-labelledby="campaign-title">
      <div class="page-actions">
        <button class="secondary" type="button" data-action="refresh">↻ Обновить</button>${trackerLink}
      </div>
      <p class="kicker">Кампания #${campaign.id}</p>
      <h1 id="campaign-title">${safe(campaign.name)}</h1>
      <dl class="campaign-meta">
        <div><dt>Статус</dt><dd><span class="badge ${campaign.state === "active" ? "active" : ""}">${status[campaign.state] || "—"}</span></dd></div>
        <div><dt>Домен</dt><dd>${safe(catalogName("domains", campaign.domain_id))}</dd></div>
        <div><dt>Группа</dt><dd>${safe(catalogName("groups", campaign.group_id))}</dd></div>
        <div><dt>Источник</dt><dd>${safe(catalogName("sources", campaign.traffic_source_id))}</dd></div>
      </dl>
    </section>
    <section class="report-section" aria-labelledby="report-title">
      <div class="section-heading">
        <div><h2 id="report-title">Статистика</h2><p id="report-range" class="muted small" aria-live="polite"></p></div>
        <div class="segments" role="group" aria-label="Период отчёта">
          ${[[1, "Сегодня"], [7, "7 дней"], [30, "30 дней"]].map(([days, label]) => `
            <button type="button" data-days="${days}" aria-pressed="${state.days === days}">${label}</button>`).join("")}
        </div>
      </div>
      <div id="metrics" class="metrics">${metricCards()}</div>
    </section>
    <section class="streams-section" aria-labelledby="streams-title">
      <div class="section-heading"><h2 id="streams-title">Потоки</h2></div>
      <div class="streams">${campaign.streams.map(renderStream).join("") ||
        '<p class="panel empty">В кампании пока нет потоков.</p>'}</div>
    </section>`;
  showReport();
}

function renderStream(stream) {
  const draft = stream.staged ? '<span class="badge draft">Черновик</span>' : "";
  const streamType = streamTypes(stream);
  const geo = stream.country_filters.map((filter) => {
    const values = Array.isArray(filter.payload) ? filter.payload.join(", ") : filter.payload;
    return `${filter.mode === "reject" ? "Кроме" : "Страна"} ${values || "—"}`;
  }).join(" · ");
  const description = [geo, typeof stream.redirect_url === "string" ? stream.redirect_url : ""]
    .filter(Boolean).join(" · ");
  return `
    <article class="panel stream ${stream.staged ? "staged" : ""}" data-stream="${stream.id}">
      <header class="stream-header">
        <div>
          <div class="stream-title"><h3>${safe(stream.name)}</h3>${draft}</div>
          <p class="muted small">#${stream.id} · ${streamType} · <span data-clicks="${stream.id}">—</span> кликов</p>
        </div>
        ${stream.staged ? `<div class="actions">
          <button class="secondary" type="button" data-action="discard">Отменить</button>
          <button class="primary" type="button" data-action="publish" ${stream.conflict || !stream.editable ? "disabled" : ""}>
            Отправить в Keitaro
          </button>
        </div>` : ""}
      </header>
      ${description ? `<p class="stream-description">${safe(description)}</p>` : ""}
      ${stream.conflict ? '<p class="stream-description warning" role="alert">Состав изменился в Keitaro. Отмените черновик, чтобы загрузить актуальные офферы.</p>' : ""}
      ${stream.editable ? renderOffers(stream) : ""}
    </article>`;
}

function streamTypes(stream) {
  if (stream.schema === "redirect") return "Редирект";
  if (stream.type === "default") return "По умолчанию";
  return ({landings: "Лендинги и офферы", action: "Действие"})[stream.schema] || "Поток";
}

function offerOptions(rows) {
  return rows.map((row) => `<option value="${row.id}">${safe(row.name)} · #${row.id}</option>`).join("");
}

function renderOffers(stream) {
  const attached = new Set(stream.offers.map((row) => row.offer_id));
  const remaining = state.offers?.active.filter((row) => !attached.has(row.id)) ?? [];
  return `
    ${stream.offers.length ? `
      <div class="table-scroll" tabindex="0" role="region" aria-label="Офферы потока">
        <table class="offer-table">
          <thead><tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col">Клики</th>
            <th scope="col">Конверсии</th><th scope="col">Продажи</th><th scope="col">Динамика кликов</th>
            <th scope="col"><span class="sr-only">Действия</span></th></tr></thead>
          <tbody>${stream.offers.map((item) => offerRow(stream, item)).join("")}</tbody>
        </table>
      </div>` : '<p class="stream-description muted">В потоке нет офферов.</p>'}
    <form class="add-offer" data-action="add">
      <input name="offer_id" type="text" inputmode="numeric" pattern="[0-9]+" list="offers-${stream.id}"
        placeholder="ID оффера или выбор из списка" aria-label="ID оффера для добавления"
        required ${stream.conflict ? "disabled" : ""}>
      <datalist id="offers-${stream.id}">
        ${remaining.map((row) => `<option value="${row.id}" label="${safe(row.name)}"></option>`).join("")}
      </datalist>
      <button type="submit" class="secondary" ${stream.conflict ? "disabled" : ""}>＋ Добавить</button>
    </form>`;
}

function offerRow(stream, item) {
  const id = item.offer_id;
  const name = state.offers?.names[id] || `Оффер #${id}`;
  const isActive = !item.removed && item.state === "active";
  const rowStatus = item.archived ? "В архиве" : item.removed ? "Будет удалён" : item.state === "disabled" ? "Отключён" : "";
  const disabled = stream.conflict ? "disabled" : "";
  return `<tr class="${item.removed ? "removed" : ""}" data-offer="${id}">
    <th scope="row"><span class="offer-name">${safe(name)}</span><span class="offer-ref">#${id}${rowStatus ? ` · ${rowStatus}` : ""}</span></th>
    <td>${isActive ? `
      <div class="share-controls">
        <form data-action="share" title="Ввод новой доли закрепляет её. Применить можно клавишей Enter.">
          <label class="sr-only" for="share-${stream.id}-${id}">Доля оффера #${id}</label>
          <input id="share-${stream.id}-${id}" name="share" type="number" min="0" max="100" value="${item.share}" required ${disabled}>
          <span aria-hidden="true">%</span>
          <button type="submit" class="mini" aria-label="Применить долю оффера #${id}" title="Применить долю" ${disabled}>✓</button>
        </form>
        <button type="button" class="mini pin ${item.pinned ? "pinned" : ""}" data-action="${item.pinned ? "unpin" : "pin"}"
            aria-label="${item.pinned ? "Открепить" : "Закрепить"} долю оффера #${id}"
            title="${item.pinned ? "Открепить долю" : "Закрепить долю"}" ${disabled}>◆</button>
      </div>` : `${item.removed ? 0 : item.share}%`}</td>
    <td data-stat="${stream.id}:${id}" data-metric="clicks">—</td>
    <td data-stat="${stream.id}:${id}" data-metric="conversions">—</td>
    <td data-stat="${stream.id}:${id}" data-metric="sales">—</td>
    <td data-stat="${stream.id}:${id}" data-metric="trend">—</td>
    <td class="offer-action"><button class="text-button ${item.removed ? "" : "danger"}" type="button"
      data-action="${item.removed ? "restore" : "remove"}" ${disabled}>${item.removed ? "Вернуть" : "Удалить"}</button></td>
  </tr>`;
}

function metricCards(stats) {
  return [["clicks", "Клики"], ["conversions", "Конверсии"], ["sales", "Продажи"]]
    .map(([metric, label]) => `<div class="panel metric"><p>${label}</p>
      <strong>${stats ? number(stats[metric]) : "—"}</strong></div>`).join("");
}

async function loadReport(days = state.days) {
  if (!state.campaign) return;
  state.days = days;
  state.report = null;
  state.reportError = "";
  const request = ++state.reportRequest;
  const nav = state.navigation;
  showReport();
  try {
    const report = await api(`/campaigns/${state.selectedId}/report?days=${days}`);
    if (nav === state.navigation && request === state.reportRequest) state.report = report;
  } catch (error) {
    if (nav === state.navigation && request === state.reportRequest) state.reportError = error.message;
  }
  if (nav === state.navigation && request === state.reportRequest) showReport();
}

function showReport() {
  const target = $("#metrics");
  if (!target) return;
  const report = state.report;
  target.innerHTML = metricCards(report?.totals);
  const range = $("#report-range");
  range.textContent = state.reportError || (report
    ? `${shortDate(report.days[0])}${report.days.length > 1 ? ` – ${shortDate(report.days.at(-1))}` : ""} · ${report.timezone}`
    : "Загружаем отчёт…");
  range.classList.toggle("warning", Boolean(state.reportError));
  for (const button of root.querySelectorAll("[data-days]")) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.days) === state.days));
  }
  for (const cell of root.querySelectorAll("[data-clicks]")) {
    cell.textContent = report ? number(report.streams[cell.dataset.clicks]?.clicks ?? 0) : "—";
  }
  for (const cell of root.querySelectorAll("[data-stat]")) {
    const stats = report?.offers[cell.dataset.stat];
    const metric = cell.dataset.metric;
    if (metric === "trend" && report) {
      const trend = stats?.trend || report.days.map(() => 0);
      const max = Math.max(...trend, 1);
      const summary = trend.map((value, i) => `${shortDate(report.days[i])}: ${number(value)}`).join(", ");
      cell.innerHTML = `<span class="trend" role="img" aria-label="${safe(summary)}" title="${safe(summary)}">
        ${trend.map((value) => `<i style="--height:${value ? Math.max(value / max * 100, 7) : 0}%"></i>`).join("")}</span>`;
    } else {
      cell.textContent = report ? number(stats?.[metric] ?? 0) : "—";
    }
  }
}

function shortDate(iso) {
  return `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
}

async function showCreate() {
  const nav = navigation("create");
  root.innerHTML = '<p class="loading">Загружаем справочники Keitaro…</p>';
  try {
    const [catalog, offers] = await Promise.all([api("/catalog"), getOffers()]);
    if (nav !== state.navigation) return;
    state.catalog = catalog;
    const select = (field, title, options) => `
      <label>${title}<select name="${field}" required>
        <option value="">Выберите</option>
        ${options.map((item) => `<option value="${item.id}" ${catalog.defaults[field] === item.id ? "selected" : ""}>${safe(item.name)}</option>`).join("")}
      </select></label>`;
    root.innerHTML = `
      <h1>Новая кампания</h1>
      <div class="create-layout">
        <section class="panel create-panel">
          <form id="create-campaign" data-action="submit-campaign">
            <label>Название кампании
              <span class="prefix-input"><span>${safe(state.status.prefix)}</span>
                <input name="name" maxlength="100" placeholder="например, aurora_au" required autofocus></span>
            </label>
            <div class="form-grid two">
              <label>Гео-код
                <input name="country" maxlength="2" minlength="2" pattern="[A-Za-z]{2}" placeholder="AU" autocapitalize="characters" required>
                <span class="field-hint">Две буквы по ISO 3166-1</span>
              </label>
              <label>Оффер
                <select name="offer_id" required><option value="">Выберите оффер</option>${offerOptions(offers.active)}</select>
              </label>
            </div>
            <div class="form-grid three">
              ${select("domain_id", "Домен", catalog.domains)}
              ${select("group_id", "Группа", catalog.groups)}
              ${select("traffic_source_id", "Источник", catalog.sources)}
            </div>
            <div class="actions"><button class="primary" type="submit">Создать кампанию</button></div>
          </form>
        </section>
        <aside class="panel creation-summary" aria-labelledby="routing-title">
          <h2 id="routing-title">Маршрутизация</h2>
          <ol><li><strong>Гео → Google</strong><span>Переходы из указанной страны.</span></li>
            <li><strong>Остальные → оффер</strong><span>Выбранный оффер получает 100% трафика второго потока.</span></li></ol>
        </aside>
      </div>`;
  } catch (error) {
    if (nav !== state.navigation) return;
    root.innerHTML = `<section class="panel error-panel"><h1>Новая кампания</h1>
      <p>${safe(error.message)}</p><button class="secondary" type="button" data-action="create">Повторить</button></section>`;
  } finally {
    if (nav === state.navigation) root.removeAttribute("aria-busy");
  }
}

async function submitCampaign(form) {
  const values = Object.fromEntries(new FormData(form));
  values.country = values.country.toUpperCase();
  const serialized = JSON.stringify(values);
  const key = "workbench:create";
  let attempt = JSON.parse(sessionStorage.getItem(key) || "null");
  if (attempt?.payload !== serialized) {
    attempt = {payload: serialized, id: crypto.randomUUID()};
    sessionStorage.setItem(key, JSON.stringify(attempt));
  }
  const campaign = await api("/campaigns", {...values, request_id: attempt.id});
  sessionStorage.removeItem(key);
  state.campaigns = [campaign, ...state.campaigns.filter((item) => item.id !== campaign.id)];
  renderList();
  return campaign;
}

async function changeStream(source, action) {
  const nav = state.navigation;
  const streamId = Number(source.closest("[data-stream]").dataset.stream);
  const stream = state.campaign.streams.find((item) => item.id === streamId);
  const base = `/campaigns/${state.selectedId}/streams/${streamId}`;
  const body = {version: stream.version};
  let updated;
  if (action === "publish" || action === "discard") {
    updated = await api(`${base}/${action}`, body);
  } else {
    const data = source.tagName === "FORM" ? new FormData(source) : null;
    body.action = action;
    body.offer_id = Number(data?.get("offer_id") || source.closest("[data-offer]")?.dataset.offer);
    if (action === "share") body.share = Number(data.get("share"));
    updated = await api(`${base}/draft`, body, "PATCH");
  }
  if (nav !== state.navigation) return;
  const oldNode = source.closest("[data-stream]");
  state.campaign.streams = state.campaign.streams.map((item) => item.id === streamId ? updated : item);
  oldNode.outerHTML = renderStream(updated);
  showReport();
  if (action === "publish") inform("Изменения сохранены в Keitaro.");
  if (action === "discard") inform("Черновик отменён.");
}

async function perform(action, source) {
  const nav = state.navigation;
  const container = source.closest("[data-stream], #create-campaign");
  const controls = [...container.querySelectorAll("button:enabled, input:enabled, select:enabled")];
  root.setAttribute("aria-busy", "true");
  inform("");
  try {
    // FormData must be captured before disabling the fields.
    const result = action === "submit-campaign" ? submitCampaign(source) : changeStream(source, action);
    controls.forEach((control) => { control.disabled = true; });
    const campaign = await result;
    if (action === "submit-campaign" && nav === state.navigation) {
      await openCampaign(campaign.id);
      if (state.selectedId === campaign.id) inform(`Кампания #${campaign.id} создана.`);
    }
  } catch (error) {
    if (nav === state.navigation) inform(error.message, true);
  } finally {
    controls.forEach((control) => { control.disabled = false; });
    if (nav === state.navigation) root.removeAttribute("aria-busy");
  }
}

root.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-action]");
  if (!form) return;
  event.preventDefault();
  perform(form.dataset.action, form);
});
root.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.days) return loadReport(Number(button.dataset.days));
  const {action} = button.dataset;
  if (action === "create") return showCreate();
  if (action === "refresh") return openCampaign(state.selectedId, true);
  if (action && button.type === "button") perform(action, button);
});
$("#new-campaign").addEventListener("click", showCreate);
$("#reload-list").addEventListener("click", () => listCampaigns());
$("#more-campaigns").addEventListener("click", () => listCampaigns(true));
$("#campaign-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-campaign]");
  if (button) openCampaign(Number(button.dataset.campaign));
});
$("#open-by-id").addEventListener("submit", (event) => {
  event.preventDefault();
  openCampaign(Number($("#campaign-id").value));
});

async function boot() {
  try {
    state.status = await api("/status");
    $("#connection").textContent = state.status.demo ? "Демо-режим" : "Keitaro Admin API";
    const requestedId = Number(new URL(location.href).searchParams.get("campaign"));
    const list = listCampaigns();
    if (Number.isSafeInteger(requestedId) && requestedId > 0) {
      await openCampaign(requestedId);
    } else {
      await list;
      if (state.navigation) return;
      if (state.status.demo && state.campaigns.length) await openCampaign(state.campaigns[0].id);
      else renderHome();
    }
  } catch {
    root.removeAttribute("aria-busy");
    root.innerHTML = '<p class="panel error-panel">Не удалось открыть приложение. Обновите страницу.</p>';
  }
}
boot();
