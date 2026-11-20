"use strict";

const content = document.querySelector("#content");
const drawer = document.querySelector("#drawer");
const csrf = document.querySelector("[name=csrf]").content;
const demo = document.body.dataset.demo === "yes";
const panelName = document.body.dataset.panelName;
const grants = new Set(document.body.dataset.grants.split(" "));
const labels = {
  knowledge: "База знаний", inventory: "IT-инвентарь", admin: "Администрирование", account: "Мой профиль",
  overview: "Обзор", statistics: "Статистика", points: "Торговые точки", history: "Журнал писем",
  people: "Получатели", iiko: "Подключение iiko", smtp: "Почта · SMTP", imap: "Почта · IMAP", monitor: "Авторассылка",
};
const statusLabels = {
  on_time: ["Вовремя", "green", "check-circle"], late_open: ["Открыта поздно", "amber", "clock"],
  not_open: ["Не открыта", "red", "alert"], waiting: ["Ожидает", "", "clock"],
  unknown: ["Не проверена", ""], api_error: ["Ошибка iiko", "blue", "alert"],
  day_off: ["Выходной", ""], paused: ["На паузе", ""],
};
const mailStates = {
  queued: ["В очереди", ""], sent: [demo ? "Симуляция" : "Принято SMTP", "green"],
  failed: ["Ошибка", "red"], cancelled: ["Отменено", ""],
};
const mailKinds = {auto: "Оповещение", followup: "Уточнение", manual: "Вручную", test: "Тест"};
const pointPriority = ["not_open", "api_error", "late_open", "waiting", "unknown", "on_time", "day_off", "paused"];
const days = {mon: "Понедельник", tue: "Вторник", wed: "Среда", thu: "Четверг", fri: "Пятница", sat: "Суббота", sun: "Воскресенье"};
const ui = {page: "overview", snapshot: null, query: "", filter: "all", currentPoint: "", statistics: {days: "7", point: ""}};
let statisticsRequest = 0;
const timeFormat = new Intl.DateTimeFormat("ru-RU", {timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit"});
const dateFormat = new Intl.DateTimeFormat("ru-RU", {timeZone: "Europe/Moscow", day: "numeric", month: "long", year: "numeric"});
const dateTimeFormat = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});
const statisticsDayFormat = new Intl.DateTimeFormat("ru-RU", {timeZone: "Europe/Moscow", day: "2-digit", month: "short"});
const statisticsTimeFormat = new Intl.DateTimeFormat("ru-RU", {timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit", second: "2-digit"});
const statisticsPercent = new Intl.NumberFormat("ru-RU", {style: "percent", maximumFractionDigits: 1});
const openingOutcomes = [
  ["on_time", "Вовремя", "green"], ["late_open", "С опозданием", "amber"],
  ["not_open", "Без открытия", "red"], ["api_error", "Ошибка iiko", "blue"],
];

function esc(value) {
  const chars = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
  return String(value ?? "").replace(/[&<>"']/g, character => chars[character]);
}
function icon(name) { return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`; }
function time(value, format = timeFormat) { return value ? format.format(new Date(value)) : "—"; }
function dateTime(value) { return value ? dateTimeFormat.format(new Date(value)) : "—"; }
function checked(value) { return value ? "checked" : ""; }
function selected(value) { return value ? "selected" : ""; }
function disabled(value) { return value ? "disabled" : ""; }
function badge(title, tone = "", mark) {
  return `<span class="status-badge ${tone}">${mark ? icon(mark) : '<span class="dot"></span>'}${esc(title)}</span>`;
}
function button(action, label, options = {}) {
  const {icon: mark, kind = "", id, disabled: inactive = false} = options;
  return `<button class="button ${kind}" type="button" data-action="${action}" ${id !== undefined ? `data-id="${esc(id)}"` : ""} ${disabled(inactive)}>
    ${mark ? icon(mark) : ""}${esc(label)}</button>`;
}
function iconButton(action, label, mark, id) {
  return `<button class="icon-button" type="button" data-action="${action}" data-id="${esc(id)}" title="${esc(label)}" aria-label="${esc(label)}">${icon(mark)}</button>`;
}
function pageHead(title, subtitle, actions = "") {
  return `<div class="page-head"><div><h1>${esc(title)}</h1><p>${subtitle}</p></div><div class="page-actions">${actions}</div></div>`;
}
function empty(mark, title, detail = "", action = "") {
  return `<div class="empty"><span class="empty-icon">${icon(mark)}</span><strong>${esc(title)}</strong>
    ${detail ? `<span>${esc(detail)}</span>` : ""}${action}</div>`;
}
function toast(message, error = false) {
  const item = document.createElement("div");
  item.className = error ? "toast error" : "toast";
  item.innerHTML = `${icon(error ? "alert" : "check-circle")}<span>${esc(message)}</span>
    <button class="icon-button" type="button" aria-label="Убрать сообщение">${icon("close")}</button>`;
  item.querySelector("button").onclick = () => item.remove();
  document.querySelector("#toasts").append(item);
  setTimeout(() => item.remove(), error ? 9000 : 5000);
}
async function api(path, method = "GET", body) {
  const headers = {Accept: "application/json"};
  if (method !== "GET") {
    if (!(body instanceof FormData)) headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(`/api${path}`, {
    method, headers, body: method === "GET" ? undefined : body instanceof FormData ? body : JSON.stringify(body ?? {}),
  });
  if (response.status === 401) {
    location.assign("/login");
    throw new Error("Войдите в панель заново.");
  }
  const json = await response.json();
  if (!response.ok) throw new Error(json.error || "Запрос не удалось выполнить.");
  return json;
}
async function refresh(renderPage = true) {
  ui.snapshot = await api("/dashboard");
  if (renderPage) {
    render();
    if (ui.page === "statistics") await loadStatistics();
    if (isWorkPage(ui.page)) await loadWork();
  }
  return ui.snapshot;
}
function route() {
  const [hash, record] = location.hash.slice(1).split("/");
  const permitted = Array.from(document.querySelectorAll("a[data-page]"), link => link.dataset.page);
  const next = permitted.includes(hash) ? hash : permitted.find(page => page !== "account") || "account";
  if (/^[a-f0-9]{16}$/.test(record || "") && isWorkPage(next)) work.selection[next] = record;
  const navigating = next !== ui.page;
  if (navigating) {
    if (drawer.open) drawer.close();
    ui.page = next;
    ui.query = "";
    ui.filter = "all";
  }
  closeModuleMenu();
  if (ui.snapshot) render();
  if (ui.snapshot && next === "statistics") loadStatistics();
  if (ui.snapshot && isWorkPage(next)) loadWork(navigating || work.page !== next || !work.data, Boolean(record));
  if (ui.snapshot && navigating && next === "history") {
    refresh(false).then(() => {
      if (ui.page === "history") render();
    }).catch(error => {
      if (ui.page === "history") toast(error.message, true);
    });
  }
}
function focusRecord() {
  const card = content.querySelector(".record-detail");
  if (!card) return;
  card.focus({preventScroll: true});
  if (window.matchMedia("(max-width: 860px)").matches) card.scrollIntoView({block: "start"});
}
function returnToRecords() {
  const list = content.querySelector(".record-layout > .card");
  if (!list) return;
  const control = list.querySelector('[aria-pressed="true"], input[type="search"]');
  if (control) control.focus({preventScroll: true});
  list.scrollIntoView({block: "start"});
}
function listButton() {
  return button("list-return", "К списку", {kind:"list-return"});
}
function closeModuleMenu() {
  document.querySelector("#module-menu").classList.remove("open");
  document.querySelector("#mobile-nav").setAttribute("aria-expanded", "false");
}
function render() {
  const pages = {overview: pointsPage, statistics: statisticsPage, points: pointsPage, people: peoplePage, history: historyPage,
    iiko: iikoPage, smtp: mailPage, imap: mailPage, monitor: monitorPage, knowledge: workPage, inventory: workPage, admin: workPage, account: workPage};
  const tab = document.querySelector(`.section-nav [data-page="${ui.page}"]`);
  const section = tab.closest(".section-nav");
  for (const group of document.querySelectorAll(".section-nav")) group.hidden = group !== section;
  for (const link of document.querySelectorAll("a[data-page]")) {
    if (link.dataset.module === section.dataset.section) link.setAttribute("aria-current", "location");
    else if (!link.dataset.module && link.dataset.page === ui.page) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  for (const shortcut of section.querySelectorAll("[data-action]")) {
    shortcut.disabled = work.page !== ui.page || !work.data;
  }
  const mode = document.querySelector("#mode");
  if (mode) {
    const active = ui.snapshot.monitor.enabled;
    mode.classList.toggle("on", active);
    mode.innerHTML = `<span class="dot"></span>Авторассылка ${active ? "включена" : "на паузе"}`;
  }
  document.title = `${labels[ui.page]} · ${panelName}`;
  content.innerHTML = pages[ui.page]();
}
function metric(key, label, count, tone = "") {
  return `<button class="metric ${tone}" type="button" data-action="filter" data-id="${key}" aria-pressed="${ui.filter === key}">
    ${esc(label)}<span class="tabular">${count}</span></button>`;
}
function search(placeholder) {
  return `<label class="search">${icon("search")}<input type="search" aria-label="${esc(placeholder)}"
    id="table-search" placeholder="${esc(placeholder)}" value="${esc(ui.query)}" autocomplete="off"></label>`;
}
function selectFilter(choices) {
  return `<select id="table-filter" aria-label="Фильтр по статусу">
    ${choices.map(([value, label]) => `<option value="${value}" ${selected(ui.filter === value)}>${label}</option>`).join("")}</select>`;
}
function includesQuery(...values) {
  const query = ui.query.toLocaleLowerCase("ru").trim();
  return !query || values.some(value => String(value || "").toLocaleLowerCase("ru").includes(query));
}
function isLate(point) { return ["late_open", "not_open"].includes(point.today.status); }
function isWaiting(point) { return ["waiting", "unknown"].includes(point.today.status); }
function pointsPage() {
  const {points, now} = ui.snapshot;
  const overview = ui.page === "overview";
  const title = overview ? "Открытие смен" : labels.points;
  const subtitle = overview ? `${icon("calendar")}${dateFormat.format(new Date(now))} · МСК` : "";
  const add = button("point-new", "Добавить точку", {icon:"plus", kind:"primary"});
  if (!points.length) return pageHead(title, subtitle, add) +
    `<section class="card">${empty("store", "Вы ещё не добавили точки", "Подключите iiko и задайте график первой точки.",
      `<a href="#iiko" class="button">${icon("link")}Настроить iiko</a>`)}</section>`;
  const filters = [["all", "Все статусы"], ["on_time", statusLabels.on_time[0]], ["late", "Все опоздания"],
    ["not_open", statusLabels.not_open[0]], ["late_open", statusLabels.late_open[0]], ["waiting", "Ожидают"],
    ["api_error", statusLabels.api_error[0]], ["day_off", "Выходной"], ["paused", "На паузе"]];
  const statuses = ["on_time", "late_open", "not_open"];
  if (points.some(point => point.today.status === "api_error")) statuses.push("api_error");
  const scores = overview ? `<div class="metrics" role="group" aria-label="Фильтры смен">
    ${metric("all", "Все точки", points.length)}${statuses.map(status => {
      const [label, tone] = statusLabels[status];
      return metric(status, label, points.filter(point => point.today.status === status).length, tone);
    }).join("")}</div>` : "";
  const latest = points.map(point => point.today.checked_at).filter(Boolean).sort().at(-1);
  const visible = filteredPoints();
  return pageHead(title, subtitle, button("check-all", "Проверить сейчас", {icon:"refresh"}) + add) + scores +
    `<div class="record-layout monitor-layout"><section class="card"><div class="card-head"><div class="card-headline"><h2>Точки сети</h2><span class="count">${points.length}</span></div>
    <div class="table-controls">${search("Поиск точки")}${selectFilter(filters)}</div></div>
    <div class="table-wrap"><table class="point-table" aria-label="Точки сети"><thead><tr><th scope="col">Точка</th>
    <th scope="col">По графику</th><th scope="col">Открыта</th><th scope="col">Статус</th></tr></thead>
    <tbody id="table-rows">${pointRows(visible)}</tbody></table></div>
    <div class="table-foot"><span>${latest ? `Последняя проверка в ${time(latest)} МСК` : "Данные ещё не проверены"}</span>
    <a href="#history" class="text-button">Журнал писем →</a></div></section>
    <div id="point-detail" aria-label="Карточка торговой точки">${pointDetail(visible)}</div></div>`;
}

function filteredPoints() {
  const people = new Map(ui.snapshot.people.map(person => [person.id, person]));
  const points = ui.snapshot.points.filter(point => {
    const status = point.today.status;
    const matches = ui.filter === "all" || ui.filter === status || ui.filter === "late" && isLate(point) ||
      ui.filter === "waiting" && isWaiting(point);
    return matches && includesQuery(point.name, ...point.people.map(id => people.get(id)?.name));
  });
  if (ui.page === "overview") points.sort((a, b) => pointPriority.indexOf(a.today.status) - pointPriority.indexOf(b.today.status));
  if (!points.some(point => point.id === ui.currentPoint)) ui.currentPoint = points[0]?.id || "";
  return points;
}

function pointRows(points) {
  return points.map(point => {
    const state = point.today;
    const active = point.id === ui.currentPoint;
    return `<tr data-action="point-select" data-id="${esc(point.id)}" class="${active ? "selected-row" : ""}">
      <td><button class="point-button" type="button" data-action="point-select" data-id="${esc(point.id)}" aria-pressed="${active}">${esc(point.name)}</button></td>
      <td class="tabular" data-label="По графику">${time(state.deadline)}</td>
      <td class="tabular" data-label="Открыта">${time(state.opened_at)}</td>
      <td data-label="Статус">${badge(...(statusLabels[state.status] || statusLabels.unknown))}</td></tr>`;
  }).join("") || `<tr><td colspan="4" class="empty-cell">${empty("search", "Точки не найдены", "Измените поиск или фильтр.")}</td></tr>`;
}

function pointDetail(visible) {
  const point = visible.find(item => item.id === ui.currentPoint);
  if (!point) return "";
  const state = point.today;
  const assigned = ui.snapshot.people.filter(person => point.people.includes(person.id) && person.active);
  return `<section class="card record-detail" tabindex="-1"><div class="card-head"><h2>${esc(point.name)}</h2>${listButton()}</div><div class="card-body stack">
    <div>${badge(...(statusLabels[state.status] || statusLabels.unknown))}</div>
    <dl class="profile-facts"><div><dt>По графику</dt><dd>${time(state.deadline)}</dd></div><div><dt>Открыта</dt><dd>${time(state.opened_at)}</dd></div>
    <div><dt>Проверка с</dt><dd>${time(state.check_at)}</dd></div><div><dt>Последняя проверка</dt><dd>${time(state.checked_at)}</dd></div>
    <div><dt>Ответственные</dt><dd>${assigned.length ? assigned.map(person => esc(person.name)).join(", ") : "Не назначены"}</dd></div></dl>
    <div class="row">${button("point-edit","График точки",{icon:"pencil",id:point.id})}
    ${button("point-mail","Предпросмотр письма",{icon:"mail",id:point.id})}</div></div></section>`;
}

function peoplePage() {
  const list = ui.snapshot.people;
  return pageHead(labels.people, "Адреса для оповещений и их торговые точки",
    button("person-new", "Добавить получателя", {icon: "plus", kind: "primary"})) + (list.length ? `<section class="card">
    <div class="card-head"><div class="card-headline"><h2>Список получателей</h2><span class="count">${list.length}</span></div>
      <div class="table-controls">${search("Имя или email")}</div></div>
    <div class="table-wrap"><table aria-label="Получатели"><thead><tr><th>Получатель</th><th>Торговые точки</th>
      <th>Статус</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody id="table-rows">${peopleRows()}</tbody></table></div>
    </section>` : `<section class="card">${empty("users", "Получателей пока нет", "Добавьте email сотрудника и выберите его точки.")}</section>`);
}
function peopleRows() {
  return ui.snapshot.people.map(person => {
    const points = ui.snapshot.points.filter(point => point.people.includes(person.id)).map(point => point.name);
    if (!includesQuery(person.name, person.email, ...points)) return "";
    const initials = person.name.split(/\s+/).slice(0, 2).map(word => word.slice(0, 1)).join("").toUpperCase();
    return `<tr data-action="person-edit" data-id="${person.id}"><td><button type="button" class="point-button contact-cell"
      data-action="person-edit" data-id="${person.id}"><span class="contact-avatar">${esc(initials)}</span>
      <span><strong>${esc(person.name)}</strong><small>${esc(person.email)}</small></span></button></td>
      <td class="people-cell">${points.length ? esc(points.join(", ")) : "Не назначены"}</td>
      <td>${badge(person.active ? "Получает письма" : "На паузе", person.active ? "green" : "")}</td>
      <td><div class="table-end">${iconButton("person-edit", "Редактировать получателя", "right", person.id)}</div></td></tr>`;
  }).join("") || `<tr><td colspan="4" class="empty-cell">${empty("search", "Получатели не найдены")}</td></tr>`;
}
function reportDate(day, format = dateFormat) {
  return format.format(new Date(`${day}T00:00:00+03:00`));
}
function openingPercent(value) {
  return value === null ? "—" : statisticsPercent.format(value / 100);
}
function openingDelay(seconds) {
  if (seconds === null) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  const rest = seconds % 60;
  if (hours) return `${hours} ч ${minutes} мин`;
  if (minutes && rest) return `${minutes} мин ${rest} сек`;
  return minutes ? `${minutes} мин` : `${rest} сек`;
}
function statisticsPage() {
  const {points} = ui.snapshot;
  if (!points.some(item => item.id === ui.statistics.point)) ui.statistics.point = "";
  const {days, point} = ui.statistics;
  return pageHead("Статистика открытий", "Результаты проверок iiko по московскому времени",
    button("refresh", "Обновить", {icon: "refresh"})) +
    `<div class="row stats-filters">
      <label>Период<select id="statistics-period">
        ${[["1", "Сегодня"], ["7", "7 дней"], ["30", "30 дней"], ["90", "90 дней"]].map(([value, label]) =>
          `<option value="${value}" ${selected(value === days)}>${label}</option>`).join("")}
      </select></label>
      <label>Точка<select id="statistics-point"><option value="">Все точки</option>
        ${points.map(item => `<option value="${esc(item.id)}" ${selected(item.id === point)}>${esc(item.name)}</option>`).join("")}
      </select></label>
    </div>
    <div id="statistics-results" class="stack" aria-busy="true"><p class="muted" role="status">Загрузка статистики…</p></div>`;
}
function dailyOpeningBar(day, max) {
  const observations = day.confirmed + day.api_error;
  const counts = openingOutcomes.map(([key, title]) => `${title} ${day[key]}`).join(", ");
  const label = `${reportDate(day.day)}. ${observations ? counts : "Нет результатов"}. Открыть день`;
  return `<button class="stats-day" type="button" data-action="statistics-day" data-id="${esc(day.day)}" aria-label="${esc(label)}" title="${esc(label)}">
    <span class="tabular">${reportDate(day.day, statisticsDayFormat)}</span>
    <span class="stats-track" aria-hidden="true">
      ${openingOutcomes.map(([key, , tone]) => `<span class="stats-${tone}" data-stats-amount="${day[key]}"></span>`).join("")}
      <span data-stats-amount="${max - observations}"></span>
    </span>
    <span class="tabular stats-rate">${openingPercent(day.on_time_percent)}</span>${icon("right")}
  </button>`;
}
function statisticsResults({totals, days, points, from_date, to_date}) {
  if (!totals.confirmed && !totals.api_error) {
    return `<section class="card">${empty("chart", "За этот период пока нет результатов", "Статистика появится после проверки открытия в iiko.")}</section>`;
  }
  const max = Math.max(...days.map(day => day.confirmed + day.api_error));
  const period = from_date === to_date ? reportDate(to_date) : `${reportDate(from_date, statisticsDayFormat)} — ${reportDate(to_date)}`;
  return `<div class="stats-scores" aria-label="Итоги за период">
      ${openingOutcomes.map(([key, title, tone]) => `<section class="card stats-score stats-${tone}">
        <h2>${title}</h2><strong class="tabular">${totals[key]}</strong>
        ${key === "on_time" && totals.confirmed ? `<small>${openingPercent(totals.on_time_percent)} из ${totals.confirmed} с результатом</small>` : ""}
        ${key === "late_open" && totals.average_delay_seconds !== null ? `<small>В среднем ${openingDelay(totals.average_delay_seconds)}</small>` : ""}
      </section>`).join("")}
    </div>
    <section class="card">
      <div class="card-head"><h2>По дням</h2><span class="muted">${esc(period)}</span></div>
      <div class="stats-legend">${openingOutcomes.map(([, title, tone]) => `<span><span class="stats-dot stats-${tone}" aria-hidden="true"></span>${title}</span>`).join("")}
        <span class="stats-legend-rate">Доля вовремя</span></div>
      <div class="stats-days" aria-label="Результаты по дням">${[...days].reverse().map(day => dailyOpeningBar(day, max)).join("")}</div>
    </section>
    <section class="card">
      <div class="card-head"><h2>По точкам</h2></div>
      <div class="table-wrap"><table class="point-table stats-table" aria-label="Статистика открытий по точкам"><thead><tr>
        <th scope="col">Точка</th>${openingOutcomes.map(([, title]) => `<th scope="col">${title}</th>`).join("")}
        <th scope="col">Доля вовремя</th><th scope="col">Среднее опоздание</th>
      </tr></thead><tbody>${points.map(row => `<tr>
        <td><button class="point-button stats-point" type="button" data-action="statistics-point" data-id="${esc(row.id)}">${esc(row.name)}</button></td>
        ${openingOutcomes.map(([key, title]) => `<td class="tabular" data-label="${title}">${row[key]}</td>`).join("")}
        <td class="tabular" data-label="Доля вовремя">${openingPercent(row.on_time_percent)}</td>
        <td class="tabular" data-label="Среднее опоздание">${openingDelay(row.average_delay_seconds)}</td>
      </tr>`).join("")}</tbody></table></div>
      <div class="table-foot"><span>Один результат на точку за день</span><span>«Без открытия» на момент последней проверки</span></div>
    </section>`;
}
async function loadStatistics() {
  const results = document.querySelector("#statistics-results");
  if (!results) return;
  const request = ++statisticsRequest;
  const params = new URLSearchParams(ui.statistics);
  const current = () => results.isConnected && request === statisticsRequest;
  results.setAttribute("aria-busy", "true");
  try {
    const report = await api(`/statistics?${params}`);
    if (!current()) return;
    results.innerHTML = statisticsResults(report);
    results.querySelectorAll("[data-stats-amount]").forEach(segment => {
      segment.style.flexGrow = segment.dataset.statsAmount;
    });
  } catch (error) {
    if (current()) results.innerHTML = `<section class="card">${empty("alert", "Статистика недоступна", error.message, button("refresh", "Повторить"))}</section>`;
  } finally {
    if (current()) results.removeAttribute("aria-busy");
  }
}
async function openStatisticsDay(day) {
  const {point} = ui.statistics;
  const report = await api(`/statistics/day/${encodeURIComponent(day)}?${new URLSearchParams({point})}`);
  if (ui.page !== "statistics" || ui.statistics.point !== point) return;
  drawerView("Открытия за день", `${reportDate(report.day)} · МСК`, `<div class="table-wrap"><table class="point-table stats-day-table" aria-label="Открытия точек за день">
      <thead><tr><th scope="col">Точка</th><th scope="col">По графику</th><th scope="col">Факт</th><th scope="col">Статус</th></tr></thead>
      <tbody>${report.points.map(row => `<tr><td>${esc(row.name)}</td><td class="tabular" data-label="По графику">${time(row.deadline, statisticsTimeFormat)}</td>
        <td class="tabular" data-label="Факт">${time(row.opened_at, statisticsTimeFormat)}</td><td>${badge(...(statusLabels[row.status] || statusLabels.unknown))}
          ${row.delay_seconds === null ? "" : `<small class="muted stats-delay">+${openingDelay(row.delay_seconds)}</small>`}
          ${row.checked_at ? `<small class="muted stats-delay">Проверено в ${time(row.checked_at, statisticsTimeFormat)}</small>` : ""}</td></tr>`).join("")}</tbody>
    </table></div>`);
}
function historyPage() {
  return pageHead(labels.history, "Автоматические оповещения, ручные отправки и тесты SMTP",
    button("refresh", "Обновить", {icon: "refresh"})) + `<section class="card">
    <div class="card-head"><div class="card-headline"><h2>Последние письма</h2><span class="count">${ui.snapshot.history.length}</span></div>
      <div class="table-controls">${search("Точка или email")}${selectFilter([
        ["all", "Все статусы"], ["sent", "Принято SMTP"], ["queued", "В очереди"], ["failed", "Ошибка"], ["cancelled", "Отменено"],
      ])}</div></div>
    <div class="table-wrap"><table aria-label="История отправки"><thead><tr><th>Время, МСК</th><th>Точка / письмо</th><th>Получатель</th><th>Статус</th>
      <th><span class="sr-only">Действия</span></th></tr></thead><tbody id="table-rows">${historyRows()}</tbody></table></div>
    </section>`;
}
function historyRows() {
  return ui.snapshot.history.filter(job => (ui.filter === "all" || job.status === ui.filter) &&
    includesQuery(job.point_name, job.recipient, job.subject)).map(job => `<tr data-action="letter-open" data-id="${job.id}">
    <td class="tabular">${dateTime(job.created_at)}</td><td>
    <button type="button" class="point-button" data-action="letter-open" data-id="${job.id}">${esc(job.point_name || "Проверка почты")}</button>
    <small class="muted"> · ${esc(mailKinds[job.kind])}</small></td><td class="wrap">${esc(job.recipient)}</td>
    <td>${badge(...mailStates[job.status])}</td><td><div class="table-end">${iconButton("letter-open", "Открыть письмо", "right", job.id)}</div></td></tr>`).join("") ||
    `<tr><td colspan="5" class="empty-cell">${empty("mail", "Писем в списке нет", "Отправьте тестовое письмо или дождитесь первой проверки.")}</td></tr>`;
}
function iikoPage() {
  const {servers} = ui.snapshot;
  return pageHead(labels.iiko, "Доступ к кассовым сменам iikoServer и iikoChain",
    button("server-new", "Добавить подключение", {icon: "plus", kind: "primary"})) +
    (servers.length ? `<div class="settings-list">${servers.map(server => `<div class="card item">
      <span class="point-icon">${icon("server")}</span><div class="item-title"><strong>${esc(server.name)}</strong>
        <small>${esc(server.base_url)} · ${esc(server.timezone)}</small></div>
      ${badge(server.health?.ok ? "Проверено" : server.health ? "Ошибка связи" : "Сохранено", server.health?.ok ? "green" : server.health ? "red" : "")}
      ${button("server-probe", "Проверить", {id: server.id, icon: "refresh"})}
      ${button("server-edit", "Настроить", {id: server.id, icon: "pencil"})}
      ${server.health && !server.health.ok ? `<p class="error-box full">${esc(server.health.message)}</p>` : ""}</div>`).join("")}</div>` :
      `<section class="card">${empty("link", "Нет подключений к iiko", "Добавьте HTTPS-адрес сервера и учётную запись для чтения смен.")}</section>`);
}
function healthBadge(configured, health) {
  if (health?.ok) return badge("Проверено", "green");
  if (health) return badge("Ошибка связи", "red");
  return badge(configured ? "Сохранено" : "Не настроено");
}
function mailPage() {
  const {smtp, smtp_health: health, imap} = ui.snapshot;
  const channel = ui.page === "imap" ? imapCard(imap) : smtpCard(smtp, health);
  return pageHead(labels[ui.page], "") + `<div class="settings-grid">${channel}
    <section class="card"><div class="card-head"><h2>Тестовое письмо</h2><span class="point-icon">${icon("send")}</span></div>
          <form class="card-body stack" data-form="smtp-test">
            <div class="error-box" data-error role="alert"></div>
            <label>Email получателя<input name="email" type="email" required placeholder="manager@example.org" value="${esc(imap.email)}">
              ${imap.email ? '<span class="hint">Проверка через IMAP доступна для адреса подключённого ящика.</span>' : ""}</label>
            <div class="row"><button class="button" ${disabled(!smtp.host)}>${icon("send")}Отправить тест</button></div>
            <div id="test-result" aria-live="polite"></div>
            <a class="text-button" href="#history">Открыть журнал писем →</a>
          </form>
        </section>
    </div>`;
}
function smtpCard(smtp, health) {
  return `<section class="card"><div class="card-head"><h2>SMTP · Отправитель</h2>${healthBadge(smtp.host, health)}</div>
        <form id="smtp-form" class="card-body stack" data-form="smtp">
          <div class="error-box" data-error role="alert"></div>
          <div class="field-grid smtp-grid">
            <label>SMTP-сервер<input name="host" required placeholder="smtp.yandex.ru" value="${esc(smtp.host)}" maxlength="253"></label>
            <label>Порт<input name="port" type="number" min="1" max="65535" required value="${smtp.port || 465}"></label>
            <label class="full">Шифрование<select name="security"><option value="ssl">SSL/TLS</option>
              <option value="starttls" ${selected(smtp.security === "starttls")}>STARTTLS</option></select></label>
            <label class="full">Адрес отправителя<input name="sender" type="email" required placeholder="alerts@example.org" value="${esc(smtp.sender)}"></label>
            <label class="full">Имя отправителя<input name="sender_name" maxlength="120" placeholder="Контроль открытия"
              value="${esc(smtp.sender_name ?? "Контроль открытия")}"></label>
            <label class="full">Логин SMTP<input name="login" autocomplete="username" placeholder="alerts@example.org" value="${esc(smtp.login)}">
              <span class="hint">Для почтового реле без входа оставьте логин пустым.</span></label>
            <label class="full">Пароль SMTP<input name="password" type="password" autocomplete="new-password"
              placeholder="${smtp.has_password ? "Пароль сохранён" : "Пароль или пароль приложения"}">
              ${smtp.has_password ? '<span class="hint">При смене сервера, порта, шифрования или логина введите пароль заново.</span>' : ""}</label>
          </div>
          <div class="row"><button class="button primary">Сохранить настройки</button>
            ${button("smtp-probe", "Проверить соединение", {icon: "refresh", disabled: !smtp.host})}</div>
          ${health ? `<div class="${health.ok ? "result-box" : "error-box"}" role="status">${esc(health.message)}</div>` : ""}
        </form>
      </section>`;
}
function imapCard(imap) {
  return `<section class="card"><div class="card-head"><h2>IMAP · Входящие</h2>${healthBadge(imap.host, imap.health)}</div>
    <form class="card-body stack" data-form="imap">
      <div class="error-box" data-error role="alert"></div>
      <div class="field-grid smtp-grid">
        <label>IMAP-сервер<input name="host" required placeholder="imap.yandex.ru" value="${esc(imap.host)}" maxlength="253"></label>
        <label>Порт<input name="port" type="number" min="1" max="65535" required value="${imap.port || 993}"></label>
        <label class="full">Шифрование<select name="security"><option value="ssl">SSL/TLS</option>
          <option value="starttls" ${selected(imap.security === "starttls")}>STARTTLS</option></select></label>
        <label class="full">Адрес ящика<input name="email" type="email" required placeholder="alerts@example.org" value="${esc(imap.email)}"></label>
        <label class="full">Логин IMAP<input name="login" required autocomplete="username" placeholder="alerts@example.org" value="${esc(imap.login)}"></label>
        <label class="full">Пароль IMAP<input name="password" type="password" autocomplete="new-password" ${imap.has_password ? "" : "required"}
          placeholder="${imap.has_password ? "Пароль сохранён" : "Пароль или пароль приложения"}">
          ${imap.has_password ? '<span class="hint">При смене сервера, порта, шифрования или логина введите пароль заново.</span>' : ""}</label>
      </div>
      <div class="row"><button class="button primary">Сохранить IMAP</button>
        ${button("imap-probe", "Проверить доступ", {icon: "refresh", disabled: !imap.host})}</div>
      ${imap.health ? `<div class="${imap.health.ok ? "result-box" : "error-box"}" role="status">${esc(imap.health.message)}</div>` : ""}
    </form>
  </section>`;
}
function monitorPage() {
  const options = ui.snapshot.monitor;
  const {smtp, points, people} = ui.snapshot;
  const ready = Boolean(smtp.host && points.some(point => point.active && point.people.some(
    id => people.find(person => person.id === id)?.active)));
  return pageHead(labels.monitor, "Правила проверки и отправки оповещений по московскому времени") +
    `<div class="settings-grid narrow"><section class="card"><div class="card-head"><h2>Параметры контроля</h2>
      ${badge(options.enabled ? "Включена" : "На паузе", options.enabled ? "green" : "")}</div>
      <form class="card-body stack" data-form="monitor">
        <div class="error-box" data-error role="alert"></div>
        <label class="check check-block"><input name="enabled" type="checkbox" ${checked(options.enabled)}>
          <span><strong>Отправлять автоматически</strong><small>Письмо об опоздании или отсутствии смены получают закреплённые ответственные.</small></span></label>
        ${ready ? "" : `<p class="notice">Для включения добавьте SMTP, активную точку и её получателей.</p>`}
        <p class="notice">Запрос смены доступен через 15 минут после открытия по графику.</p>
        <div class="field-grid">
          <label>Интервал проверки, сек<input name="poll_seconds" type="number" min="5" max="300" required value="${options.poll_seconds}"></label>
        </div>
        <label class="check check-block"><input name="inclusive" type="checkbox" ${checked(options.inclusive)}>
          <span><strong>Допуск 1 минута к графику открытия</strong><small>При графике 09:00 открытие до 09:01:00 включительно считается своевременным.</small></span></label>
        <div class="row"><button class="button primary">Сохранить настройки</button></div>
      </form></section></div>`;
}

function drawerView(title, hint, body, options = {}) {
  const {form = "", id, submit, extra = "", wide = false} = options;
  drawer.classList.toggle("document-drawer", wide);
  drawer.innerHTML = `<form class="drawer-form" data-form="${form}" data-id="${esc(id)}">
    <header class="drawer-head"><div><h2 id="drawer-title">${esc(title)}</h2>${hint ? `<p>${esc(hint)}</p>` : ""}</div>
      ${iconButton("drawer-close", "Закрыть", "close", "")}</header>
    <div class="drawer-body stack"><div class="error-box" data-error role="alert"></div>${body}</div>
    <footer class="drawer-foot">${extra}${button("drawer-close", submit ? "Отмена" : "Закрыть", {kind: "subtle"})}
      ${submit ? `<button class="button primary" type="submit">${esc(submit)}</button>` : ""}</footer></form>`;
  if (!drawer.open) drawer.showModal();
}
function removeButton(entity, id) {
  return id ? `<div class="spacer">${button("delete-" + entity, "Удалить", {id, kind: "danger subtle"})}</div>` : "";
}
function connectionForm(id) {
  const server = ui.snapshot.servers.find(item => item.id === id) || {};
  drawerView(id ? "Настроить iiko" : "Новое подключение iiko", "Параметры сервера и технического пользователя", `
    <div class="field-grid">
      <label class="full">Название<input name="name" required maxlength="160" value="${esc(server.name)}" placeholder="iikoChain · Москва"></label>
      <label class="full">Адрес iiko<input name="base_url" type="url" required maxlength="310"
        placeholder="https://chain.example.org/resto" value="${esc(server.base_url)}">
        <span class="hint">HTTPS-адрес должен заканчиваться на /resto.</span></label>
      <label class="full">Часовой пояс сервера<input name="timezone" required value="${esc(server.timezone || "Europe/Moscow")}" list="zones">
        <datalist id="zones"><option value="Europe/Moscow"><option value="Europe/Kaliningrad"><option value="Asia/Yekaterinburg">
          <option value="Asia/Novosibirsk"><option value="Asia/Vladivostok"><option value="UTC"></datalist>
        <span class="hint">Используется, если iiko возвращает время без смещения. Графики точек задаются по МСК.</span></label>
      <label class="full">Логин iiko<input name="login" required autocomplete="username" value="${esc(server.login)}"></label>
      <label class="full">Пароль iiko<input name="password" type="password" autocomplete="new-password"
        ${server.has_password ? 'placeholder="Пароль сохранён"' : "required"}>
        ${server.has_password ? '<span class="hint">При смене адреса или логина введите пароль заново.</span>' : ""}</label>
    </div>`, {form: "server", id, submit: "Сохранить", extra: removeButton("servers", id)});
}
function checklist(items, name, selectedIds, label, description) {
  if (!items.length) return `<p class="notice">${esc(label)}</p>`;
  return `<div class="list-checks">${items.map(item => `<label class="check">
    <input type="checkbox" name="${name}" value="${item.id}" ${checked(selectedIds.includes(item.id))}>
    <span>${esc(item.name)}</span>${description ? `<small>${esc(description(item))}</small>` : ""}</label>`).join("")}</div>`;
}
function personForm(id) {
  const person = ui.snapshot.people.find(item => item.id === id) || {};
  const selectedIds = ui.snapshot.points.filter(point => point.people.includes(id)).map(point => point.id);
  drawerView(id ? "Редактировать получателя" : "Новый получатель", "Адрес для писем и закреплённые точки", `
    <div class="field-grid">
      <label class="full">Имя<input name="name" maxlength="120" value="${esc(person.name)}" placeholder="Анна Смирнова"></label>
      <label class="full">Email<input name="email" type="email" required value="${esc(person.email)}" placeholder="manager@example.org"></label>
    </div>
    <label class="check"><input name="active" type="checkbox" ${checked(person.active !== false)}>Получает письма</label>
    <section class="form-section"><h3>Торговые точки</h3>
      ${checklist(ui.snapshot.points, "point", selectedIds, "Точки ещё не добавлены.")}</section>`,
  {form: "person", id, submit: "Сохранить", extra: removeButton("people", id)});
}
function weekRows(week) {
  return Object.entries(days).map(([key, name]) => {
    const value = week?.[key] || "09:00";
    const off = value === "off";
    return `<div class="schedule-row"><span class="schedule-day">${name}</span>
      <input type="time" aria-label="${name} — время открытия" name="time-${key}" value="${off ? "09:00" : esc(value)}" required ${disabled(off)}>
      <label class="check"><input type="checkbox" name="off-${key}" data-disable-time ${checked(off)}>Выходной</label></div>`;
  }).join("");
}
function exceptionRow(day = "", value = "09:00") {
  const off = value === "off";
  return `<div class="exception-row"><input type="date" aria-label="Дата разового изменения" required value="${esc(day)}">
    <input type="time" aria-label="Время открытия на выбранную дату" value="${off ? "09:00" : esc(value)}" required ${disabled(off)}>
    <label class="check"><input type="checkbox" data-disable-time ${checked(off)}>Выходной</label>
    ${iconButton("exception-remove", "Удалить разовое изменение", "close", "")}</div>`;
}
function pointStatus(point) {
  const current = point.today;
  if (!current) return "";
  return `<dl class="point-facts" aria-label="Открытие сегодня по московскому времени">
    <div><dt>Статус</dt><dd>${badge(...(statusLabels[current.status] || statusLabels.unknown))}</dd></div>
    <div><dt>По графику · МСК</dt><dd class="tabular">${time(current.deadline)}</dd></div>
    <div><dt>Открыта · МСК</dt><dd class="tabular">${time(current.opened_at)}</dd></div>
  </dl>
    ${current.status === "waiting" && current.check_at ? `<p class="muted">Проверка с ${time(current.check_at)} МСК.</p>` : ""}
    <div class="row">${button("check-point", "Проверить", {id: point.id, icon: "refresh"})}
    ${button("point-mail", "Предпросмотр письма", {id: point.id, icon: "mail"})}</div>
    ${current.error ? `<p class="error-box">${esc(current.error)}</p>` : ""}`;
}
function pointForm(id) {
  const point = ui.snapshot.points.find(item => item.id === id) || {};
  if (!ui.snapshot.servers.length) {
    drawerView("Добавить торговую точку", "", empty("link", "Сначала подключите iiko",
      "Для точки потребуется действующее подключение iiko и UUID предприятия.",
      button("go-iiko", "Настроить iiko", {kind: "primary", icon: "link"})));
    return;
  }
  const connectionFields = `<div class="form-section"><div class="field-grid">
      <label class="full">Подключение iiko<select name="server" required>
        ${ui.snapshot.servers.map(server => `<option value="${server.id}" ${selected(point.server === server.id)}>${esc(server.name)}</option>`).join("")}
      </select></label>
      <label class="full">UUID предприятия<input name="department_id" value="${esc(point.department_id)}" required maxlength="36"
        placeholder="00000000-0000-0000-0000-000000000000" spellcheck="false"></label>
    </div>
    <div class="row">${button("departments", "Выбрать из iiko", {icon: "store"})}</div>
    <div id="departments"></div>
    <details><summary>Фильтр по кассам и группе</summary><div class="field-grid">
      <label class="full">UUID группы касс<input name="group_id" maxlength="36" value="${esc(point.group_id)}" placeholder="Необязательно"></label>
      <label class="full">Номера касс<input name="registers" value="${esc(point.registers?.join(", "))}" placeholder="Например, 1, 2"></label>
      <label class="check full"><input name="require_all_registers" type="checkbox" ${checked(point.require_all_registers)}>Дождаться всех выбранных касс</label>
    </div></details></div>`;
  drawerView(id ? point.name : "Новая торговая точка", id ? "График и получатели оповещений" : "Выберите предприятие и задайте график", `
    ${id ? `<div class="stack point-status" data-point-state>${pointStatus(point)}</div>` : ""}
    <section class="form-section">
      <label>Название точки<input name="name" value="${esc(point.name)}" required maxlength="120" placeholder="Арбат"></label>
      <label class="check"><input name="active" type="checkbox" ${checked(point.active !== false)}>Проверять эту точку</label>
      ${id ? "" : connectionFields}
    </section>
    <section class="form-section"><h3>Обычная неделя · МСК</h3><div class="schedule">${weekRows(point.week)}</div></section>
    <section class="form-section"><div class="row"><h3 class="grow">Разовое изменение</h3>${button("exception-add", "Добавить дату", {icon: "plus"})}</div>
      <p class="hint">Укажите другое время открытия по МСК или выходной на выбранную дату.</p>
      <div class="exception-list" id="exceptions">${Object.entries(point.exceptions || {}).sort().map(([day, value]) => exceptionRow(day, value)).join("")}</div></section>
    <section class="form-section"><h3>Получатели</h3>
      ${checklist(ui.snapshot.people, "person", point.people || [], "Добавьте email в разделе «Получатели».",
        person => person.active ? person.email : `${person.email} · На паузе`)}</section>
    ${id ? `<details class="point-connection"><summary>Подключение iiko и кассы</summary>${connectionFields}</details>` : ""}`,
  {form: "point", id, submit: "Сохранить", extra: removeButton("points", id)});
}
async function mailForm(id) {
  const point = ui.snapshot.points.find(item => item.id === id);
  const recipients = ui.snapshot.people.filter(person => person.active && point.people.includes(person.id));
  const preview = recipients.length ? await api(`/points/${id}/preview`, "POST", {}) : null;
  drawerView("Отправить письмо", point.name, `
    <section class="form-section"><h3>Кому отправить</h3>
      ${checklist(recipients, "person", recipients.map(person => person.id), "Назначьте хотя бы одного активного получателя.",
        person => person.email)}</section>
    ${preview ? `<label>Комментарий к письму<textarea name="note" placeholder="Необязательно" maxlength="2000"></textarea></label>
      <div class="row"><h3 class="grow">Предпросмотр</h3>${button("letter-preview", "Обновить текст", {id, icon: "refresh"})}</div>
      <div id="letter-preview" class="letter-preview">${letterPreview(preview)}</div>
      ${ui.snapshot.smtp.host ? "" : '<p class="notice">Сохраните параметры отправителя в разделе «Почта».</p>'}` : ""}`,
  {form: "send", id, submit: recipients.length && ui.snapshot.smtp.host ? "Отправить письмо" : null});
}
function letterPreview(job) {
  return `<strong>${esc(job.subject)}</strong>${esc(job.body)}`;
}
function receiptCheck(job) {
  if (!job.can_check_imap) return "";
  return `<div class="stack" data-imap-receipt>
    <div class="row">${button("imap-receipt", "Найти во входящих", {id: job.id, icon: "search"})}</div>
    <p class="result-box" data-receipt-result role="status"></p>
  </div>`;
}
async function openLetter(id) {
  const job = await api(`/history/${id}`);
  drawerView(job.point_name || "Тестовое письмо", job.recipient, `<div class="row">${badge(...mailStates[job.status])}
      <span class="muted">${mailKinds[job.kind]} · ${dateTime(job.created_at)} МСК</span></div>
    ${job.error ? `<p class="error-box">${esc(job.error)}</p>` : ""}
    ${receiptCheck(job)}
    <div class="letter-preview">${letterPreview(job)}</div>`,
  {extra: job.status === "failed" ? button("retry-letter", "Повторить отправку", {id, icon: "refresh"}) : ""});
}
function pointPayload(form, values) {
  const week = Object.fromEntries(Object.keys(days).map(day => [
    day, values.has(`off-${day}`) ? "off" : values.get(`time-${day}`),
  ]));
  const exceptions = {};
  for (const row of form.querySelectorAll(".exception-row")) {
    const day = row.querySelector("[type=date]").value;
    if (Object.hasOwn(exceptions, day)) throw new Error("Для одной даты можно задать только одно исключение.");
    exceptions[day] = row.querySelector("[type=checkbox]").checked ? "off" : row.querySelector("[type=time]").value;
  }
  const registerText = values.get("registers").trim();
  if (registerText && !/^\d+(?:\s*,\s*\d+)*$/.test(registerText)) throw new Error("Укажите номера касс через запятую.");
  const registers = registerText ? registerText.split(",").map(Number) : [];
  return {name: values.get("name"), server: values.get("server"), department_id: values.get("department_id"),
    group_id: values.get("group_id"), active: values.has("active"), require_all_registers: values.has("require_all_registers"),
    week, exceptions, registers, people: values.getAll("person")};
}
async function saveEntity(entity, id, payload) {
  await api(`/${entity}${id ? "/" + id : ""}`, id ? "PUT" : "POST", payload);
  drawer.close();
  await refresh();
  toast("Сохранено.");
}
async function trackTest(id, container) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 900));
    if (!container.isConnected) return;
    try {
      const job = await api(`/history/${id}`);
      if (job.status === "sent") {
        const message = demo ? "Тест на демонстрационных данных завершён." : "Письмо принято SMTP.";
        container.innerHTML = `<p class="result-box">${message}</p>${receiptCheck(job)}`;
        return;
      }
      if (job.status === "failed") {
        container.innerHTML = `<p class="error-box">${esc(job.error || "SMTP отклонил письмо.")}</p>`;
        return;
      }
    } catch {
      return;
    }
  }
  if (container.isConnected) container.innerHTML = '<p class="notice">Письмо в очереди. Статус доступен в журнале.</p>';
}
async function handleForm(form) {
  if (form.dataset.form.startsWith("work-")) return submitWork(form);
  const values = new FormData(form);
  const id = form.dataset.id;
  switch (form.dataset.form) {
    case "server":
      return saveEntity("servers", id, Object.fromEntries(values));
    case "person":
      return saveEntity("people", id, {name: values.get("name"), email: values.get("email"), active: values.has("active"),
        points: values.getAll("point")});
    case "point":
      return saveEntity("points", id, pointPayload(form, values));
    case "smtp":
      await api("/smtp", "PUT", {...Object.fromEntries(values), port: Number(values.get("port"))});
      await refresh();
      toast("Настройки SMTP сохранены.");
      return;
    case "imap":
      await api("/imap", "PUT", {...Object.fromEntries(values), port: Number(values.get("port"))});
      await refresh();
      toast("Настройки IMAP сохранены.");
      return;
    case "smtp-test": {
      const job = await api("/smtp/test", "POST", {email: values.get("email")});
      const result = document.createElement("div");
      result.className = "stack";
      result.innerHTML = '<p class="notice">Тестовое письмо в очереди.</p>';
      form.querySelector("#test-result").replaceChildren(result);
      trackTest(job.id, result);
      return;
    }
    case "monitor":
      await api("/monitor", "PUT", {enabled: values.has("enabled"), inclusive: values.has("inclusive"),
        poll_seconds: Number(values.get("poll_seconds"))});
      await refresh();
      toast("Параметры авторассылки сохранены.");
      return;
    case "send": {
      const result = await api(`/points/${id}/send`, "POST", {note: values.get("note") || "", people: values.getAll("person")});
      drawer.close();
      await refresh();
      toast(`Писем в очереди: ${result.ids.length}. Статус в журнале.`);
    }
  }
}
async function runCheck(id) {
  const result = await api("/check", "POST", id ? {point: id} : {});
  await refresh();
  const section = drawer.open ? drawer.querySelector("[data-point-state]") : null;
  const point = ui.snapshot.points.find(item => item.id === id);
  if (point && section?.closest("form").dataset.id === id) section.innerHTML = pointStatus(point);
  toast(result.checked ? `Проверено точек: ${result.checked}.` : "Нет точек, готовых к проверке. Смены доступны для проверки через 15 минут после открытия по графику.");
}
async function loadDepartments(form) {
  const {items} = await api(`/servers/${form.elements.server.value}/departments`, "POST");
  const box = form.querySelector("#departments");
  const rows = items.filter(item => item.id && item.name);
  box.innerHTML = rows.length ? `<label>Торговое предприятие<select id="department-choice">
    <option value="">Выберите из списка</option>${rows.map(item =>
      `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.id.slice(0, 8))}</option>`).join("")}
    </select></label>` : '<p class="notice">Справочник пуст. Введите UUID предприятия вручную.</p>';
}
async function remove(action, id) {
  const entity = action.slice("delete-".length);
  const target = {points: "торговую точку", people: "получателя", servers: "подключение iiko"}[entity];
  if (!window.confirm(`Удалить ${target}?`)) return;
  await api(`/${entity}/${id}`, "DELETE");
  drawer.close();
  await refresh();
  toast("Запись удалена.");
}
const actions = {
  ...workActions,
  "point-select": id => { ui.currentPoint = id; updateRows(); focusRecord(); },
  "list-return": returnToRecords,
  "statistics-day": openStatisticsDay,
  "statistics-point": async id => {
    ui.statistics.point = id;
    const filter = document.querySelector("#statistics-point");
    filter.value = id;
    filter.focus();
    await loadStatistics();
  },
  "point-new": () => pointForm(),
  "point-edit": id => pointForm(id),
  "point-mail": mailForm,
  "person-new": () => personForm(),
  "person-edit": personForm,
  "server-new": () => connectionForm(),
  "server-edit": connectionForm,
  "letter-open": openLetter,
  "drawer-close": () => drawer.close(),
  "go-iiko": () => {drawer.close(); location.hash = "iiko";},
  filter: id => {ui.filter = id; render();},
  refresh: () => refresh(),
  "check-all": () => runCheck(),
  "check-point": runCheck,
  departments: (_, node) => loadDepartments(node.closest("form")),
  "exception-add": (_, node) => node.closest("form").querySelector("#exceptions").insertAdjacentHTML("beforeend", exceptionRow()),
  "exception-remove": (_, node) => node.closest(".exception-row").remove(),
  "server-probe": async id => {
    try {
      await api(`/servers/${id}/probe`, "POST");
      toast("iiko принял учётные данные.");
    } finally {
      await refresh();
    }
  },
  "smtp-probe": async () => {
    try {
      await api("/smtp/probe", "POST");
      toast("Соединение с SMTP установлено.");
    } finally {
      await refresh();
    }
  },
  "imap-probe": async () => {
    try {
      await api("/imap/probe", "POST");
      toast("Входящие доступны по IMAP.");
    } finally {
      await refresh();
    }
  },
  "imap-receipt": async (id, node) => {
    const result = node.closest("[data-imap-receipt]").querySelector("[data-receipt-result]");
    result.className = "notice";
    result.textContent = "Ищем письмо во входящих…";
    try {
      const {found} = await api(`/history/${id}/imap`, "POST");
      result.className = found ? "result-box" : "notice";
      const received = demo ? "Симуляция получения письма." : "Письмо найдено во входящих.";
      result.textContent = found ? received : "Письмо пока не найдено. Повторите проверку чуть позже.";
    } catch (error) {
      result.className = "error-box";
      result.textContent = error.message;
    }
  },
  "letter-preview": async (id, node) => {
    const form = node.closest("form");
    const job = await api(`/points/${id}/preview`, "POST", {note: form.elements.note.value});
    form.querySelector("#letter-preview").innerHTML = letterPreview(job);
  },
  "retry-letter": async id => {
    await api(`/history/${id}/retry`, "POST");
    drawer.close();
    await refresh();
    toast("Письмо возвращено в очередь.");
  },
};

function showError(node, message) {
  const box = node?.closest("form")?.querySelector("[data-error]");
  if (box && box.isConnected) {
    box.textContent = message;
    box.scrollIntoView({block: "nearest"});
  } else {
    toast(message, true);
  }
}
document.addEventListener("submit", async event => {
  const form = event.target;
  if (!form.dataset.form) return;
  event.preventDefault();
  if (form.dataset.busy) return;
  form.dataset.busy = "yes";
  const submit = form.querySelector('button[type=submit], button:not([type])');
  if (submit) submit.disabled = true;
  const box = form.querySelector("[data-error]");
  if (box) box.textContent = "";
  try {
    await handleForm(form);
  } catch (error) {
    showError(form, error.message);
  } finally {
    delete form.dataset.busy;
    if (submit) submit.disabled = false;
  }
});
document.addEventListener("click", async event => {
  const node = event.target.closest("[data-action]");
  if (!node || node.disabled || node.dataset.busy) return;
  const action = node.dataset.action;
  const run = actions[action];
  if (!run && !action.startsWith("delete-")) return;
  if (drawer.open && drawer.querySelector(".drawer-form")?.dataset.busy) return;
  if (node.tagName === "BUTTON") node.disabled = true;
  node.dataset.busy = "yes";
  try {
    if (run) await run(node.dataset.id, node);
    else await remove(action, node.dataset.id);
  } catch (error) {
    showError(node, error.message);
  } finally {
    delete node.dataset.busy;
    if (node.tagName === "BUTTON") node.disabled = false;
  }
});
function updateRows() {
  const rows = document.querySelector("#table-rows");
  if (!rows) return;
  if (ui.page === "history") rows.innerHTML = historyRows();
  else if (ui.page === "people") rows.innerHTML = peopleRows();
  else {
    const visible = filteredPoints();
    rows.innerHTML = pointRows(visible);
    document.querySelector("#point-detail").innerHTML = pointDetail(visible);
  }
}
document.addEventListener("input", event => {
  if (event.target.id === "table-search") {
    ui.query = event.target.value;
    updateRows();
  }
});
document.addEventListener("change", event => {
  const node = event.target;
  if (node.id === "statistics-period" || node.id === "statistics-point") {
    ui.statistics[node.id === "statistics-period" ? "days" : "point"] = node.value;
    loadStatistics();
  } else if (node.id === "table-filter") {
    ui.filter = node.value;
    updateRows();
    content.querySelectorAll(".metric").forEach(item => item.setAttribute("aria-pressed", String(item.dataset.id === ui.filter)));
  } else if (node.hasAttribute("data-disable-time")) {
    node.closest(".schedule-row, .exception-row").querySelector("input[type=time]").disabled = node.checked;
  } else if (node.id === "department-choice" && node.value) {
    node.closest("form").elements.department_id.value = node.value;
  } else if (node.name === "server" && node.closest("[data-form=point]")) {
    node.closest("form").querySelector("#departments").replaceChildren();
  } else if (node.name === "security") {
    const form = node.closest("form");
    const ports = {smtp: {ssl: "465", starttls: "587"}, imap: {ssl: "993", starttls: "143"}}[form?.dataset.form];
    if (ports && [ports.ssl, ports.starttls].includes(form.elements.port.value)) {
      form.elements.port.value = ports[node.value];
    }
  }
});
document.querySelector("#mobile-nav").addEventListener("click", event => {
  const isOpen = document.querySelector("#module-menu").classList.toggle("open");
  event.currentTarget.setAttribute("aria-expanded", String(isOpen));
});
document.querySelector("#module-menu").addEventListener("click", closeModuleMenu);
document.querySelector(".skip-link").addEventListener("click", event => {
  event.preventDefault();
  content.focus();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && document.querySelector("#module-menu").classList.contains("open")) {
    closeModuleMenu();
    document.querySelector("#mobile-nav").focus();
  }
});
document.addEventListener("click", event => {
  if (!event.target.closest(".app-header")) closeModuleMenu();
});
drawer.addEventListener("cancel", event => {
  if (drawer.querySelector(".drawer-form")?.dataset.busy) event.preventDefault();
});
drawer.addEventListener("invalid", event => {
  const details = event.target.closest("details");
  if (details) details.open = true;
}, true);
window.addEventListener("hashchange", route);
route();
refresh().catch(error => {
  content.innerHTML = `<section class="card">${empty("alert", "Панель не загрузилась", error.message, button("refresh", "Повторить"))}</section>`;
});
setInterval(() => {
  if (ui.snapshot && !drawer.open && ["overview", "points", "history"].includes(ui.page) &&
      !["INPUT", "SELECT"].includes(document.activeElement.tagName)) refresh().catch(() => {});
}, 20000);
