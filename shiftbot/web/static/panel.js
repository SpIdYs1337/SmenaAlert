"use strict";

const content = document.querySelector("#content");
const drawer = document.querySelector("#drawer");
const csrf = document.querySelector("[name=csrf]").content;
const demo = document.body.dataset.demo === "yes";
const panelName = document.body.dataset.panelName;
const labels = {
  overview: "Обзор", points: "Торговые точки", history: "Журнал писем",
  people: "Получатели", iiko: "Подключение iiko", smtp: "Почта SMTP", monitor: "Авторассылка",
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
const mailKinds = {auto: "Опоздание", followup: "Уточнение", manual: "Вручную", test: "Тест"};
const pointPriority = ["not_open", "api_error", "late_open", "waiting", "unknown", "on_time", "day_off", "paused"];
const days = {mon: "Понедельник", tue: "Вторник", wed: "Среда", thu: "Четверг", fri: "Пятница", sat: "Суббота", sun: "Воскресенье"};
const ui = {page: "overview", snapshot: null, query: "", filter: "all"};
const timeFormat = new Intl.DateTimeFormat("ru-RU", {timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit"});
const dateFormat = new Intl.DateTimeFormat("ru-RU", {timeZone: "Europe/Moscow", day: "numeric", month: "long", year: "numeric"});
const dateTimeFormat = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});

function esc(value) {
  const chars = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
  return String(value ?? "").replace(/[&<>"']/g, character => chars[character]);
}
function icon(name) { return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`; }
function time(value) { return value ? timeFormat.format(new Date(value)) : "—"; }
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
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(`/api${path}`, {
    method, headers, body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
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
  if (renderPage) render();
  return ui.snapshot;
}
function route() {
  const hash = location.hash.slice(1);
  const next = Object.hasOwn(labels, hash) ? hash : "overview";
  const navigating = next !== ui.page;
  if (navigating) {
    ui.page = next;
    ui.query = "";
    ui.filter = "all";
  }
  document.querySelector("#sidebar").classList.remove("open");
  document.querySelector("#mobile-nav").setAttribute("aria-expanded", "false");
  if (ui.snapshot) render();
  if (ui.snapshot && navigating && next === "history") {
    refresh(false).then(() => {
      if (ui.page === "history") render();
    }).catch(error => {
      if (ui.page === "history") toast(error.message, true);
    });
  }
}
function render() {
  const pages = {overview: pointsPage, points: pointsPage, people: peoplePage, history: historyPage,
    iiko: iikoPage, smtp: smtpPage, monitor: monitorPage};
  for (const link of document.querySelectorAll(".nav [data-page]")) {
    if (link.dataset.page === ui.page) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.querySelector("#crumb").textContent = labels[ui.page];
  const mode = document.querySelector("#mode");
  const active = ui.snapshot.monitor.enabled;
  mode.classList.toggle("on", active);
  mode.innerHTML = `<span class="dot"></span>Авторассылка ${active ? "включена" : "на паузе"}`;
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
  const subtitle = overview ? `${icon("calendar")}${dateFormat.format(new Date(now))} · МСК` :
    "Графики открытия и ответственные за каждую точку";
  if (!points.length) {
    return pageHead(overview ? "Открытие точек" : labels.points, subtitle, button("point-new", "Добавить точку", {icon: "plus", kind: "primary"})) +
      `<section class="card">${empty("store", "Вы ещё не добавили точки", "Подключите iiko и задайте график первой точки.",
        `<a href="#iiko" class="button">${icon("link")}Настроить iiko</a>`)}</section>`;
  }
  const filters = [["all", "Все статусы"], ["on_time", statusLabels.on_time[0]], ["late", "Все опоздания"],
    ["not_open", statusLabels.not_open[0]], ["late_open", statusLabels.late_open[0]], ["waiting", "Ожидают"],
    ["api_error", statusLabels.api_error[0]], ["day_off", "Выходной"], ["paused", "На паузе"]];
  const scores = overview ? `<div class="metrics" role="group" aria-label="Фильтры смен">
    ${metric("all", "Все точки", points.length)}
    ${["not_open", "late_open", "api_error"].map(status => {
      const [label, tone] = statusLabels[status];
      return metric(status, label, points.filter(point => point.today.status === status).length, tone);
    }).join("")}
  </div>` : "";
  const latest = points.map(point => point.today.checked_at).filter(Boolean).sort().at(-1);
  return pageHead(overview ? "Открытие точек" : labels.points, subtitle,
    button("check-all", "Проверить сейчас", {icon: "refresh"}) + button("point-new", "Добавить точку", {icon: "plus", kind: "primary"})) +
    scores + `<section class="card">
    <div class="card-head"><div class="card-headline"><h2>Точки сети</h2><span class="count">${points.length}</span></div>
      <div class="table-controls">${search("Поиск точки")}${overview ? "" : selectFilter(filters)}</div></div>
    <div class="table-wrap"><table class="point-table" aria-label="Точки сети"><thead><tr>
      <th scope="col">Точка</th><th scope="col">По графику</th><th scope="col">Открыта</th><th scope="col">Статус</th>
      <th scope="col">Ответственные</th><th scope="col"><span class="sr-only">Действия</span></th>
    </tr></thead><tbody id="table-rows">${pointRows()}</tbody></table></div>
    <div class="table-foot"><span>${latest ? `Последняя проверка в ${time(latest)} МСК` : "Данные ещё не проверены"}</span>
      <a href="#history" class="text-button">Журнал писем <span aria-hidden="true">→</span></a></div>
    </section>`;
}
function pointRows() {
  const people = new Map(ui.snapshot.people.map(person => [person.id, person]));
  const points = ui.snapshot.points.filter(point => {
    const status = point.today.status;
    const matches = ui.filter === "all" || ui.filter === status || ui.filter === "late" && isLate(point) ||
      ui.filter === "waiting" && isWaiting(point);
    return matches && includesQuery(point.name, ...point.people.map(id => people.get(id)?.name));
  });
  if (ui.page === "overview") {
    points.sort((left, right) => pointPriority.indexOf(left.today.status) - pointPriority.indexOf(right.today.status));
  }
  return points.map(point => {
    const state = point.today;
    const assigned = point.people.map(id => people.get(id)).filter(person => person?.active);
    return `<tr data-action="point-edit" data-id="${point.id}">
      <td><button class="point-button" type="button" data-action="point-edit" data-id="${point.id}">
        <span class="point-icon">${icon("store")}</span>${esc(point.name)}</button></td>
      <td class="tabular" data-label="По графику">${time(state.deadline)}</td>
      <td class="tabular ${!state.opened_at ? "muted" : ""}" data-label="Открыта">${time(state.opened_at)}</td>
      <td data-label="Статус">${badge(...(statusLabels[state.status] || statusLabels.unknown))}</td>
      <td class="people-cell" data-label="Ответственные">${assigned.length ? esc(assigned.map(person => person.name).join(", ")) : "Не назначены"}</td>
      <td><div class="table-end">${iconButton("point-mail", "Предпросмотр письма", "mail", point.id)}
        ${iconButton("point-edit", "Редактировать точку", "right", point.id)}</div></td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="empty-cell">${empty("search", "Точки не найдены", "Измените поиск или фильтр.")}</td></tr>`;
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
function smtpPage() {
  const {smtp, smtp_health: health} = ui.snapshot;
  return pageHead(labels.smtp, "Служебный почтовый ящик для отправки оповещений") +
    `<div class="settings-grid">
      <section class="card"><div class="card-head"><h2>Отправитель</h2>${healthBadge(smtp.host, health)}</div>
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
      </section>
      <section class="card"><div class="card-head"><h2>Тестовое письмо</h2><span class="point-icon">${icon("send")}</span></div>
        <form class="card-body stack" data-form="smtp-test">
          <p class="muted">Отправьте пробное письмо на один адрес и проверьте его в папке «Входящие».</p>
          <div class="error-box" data-error role="alert"></div>
          <label>Email получателя<input name="email" type="email" required placeholder="manager@example.org"></label>
          <div class="row"><button class="button" ${disabled(!smtp.host)}>${icon("send")}Отправить тест</button></div>
          <p id="test-result" class="result-box" role="status"></p>
          <a class="text-button" href="#history">Открыть журнал писем →</a>
        </form>
      </section>
    </div>`;
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
          <span><strong>Отправлять автоматически</strong><small>При опоздании письмо получают назначенные сотрудники.</small></span></label>
        ${ready ? "" : `<p class="notice">Для включения добавьте SMTP, активную точку и её получателей.</p>`}
        <div class="field-grid">
          <label>Период проверки, сек<input name="poll_seconds" type="number" min="5" max="300" required value="${options.poll_seconds}"></label>
          <label>Задержка уведомления, сек<input name="delay_seconds" type="number" min="0" max="3600" required value="${options.delay_seconds}">
            <span class="hint">Позволяет дождаться синхронизации с iiko.</span></label>
        </div>
        <label class="check"><input name="inclusive" type="checkbox" ${checked(options.inclusive)}>Открытие ровно по графику считается своевременным</label>
        <div class="row"><button class="button primary">Сохранить настройки</button></div>
      </form></section></div>`;
}

function drawerView(title, hint, body, options = {}) {
  const {form = "", id, submit, extra = ""} = options;
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
      ${ui.snapshot.smtp.host ? "" : '<p class="notice">Сохраните параметры отправителя в разделе «Почта SMTP».</p>'}` : ""}`,
  {form: "send", id, submit: recipients.length && ui.snapshot.smtp.host ? "Отправить письмо" : null});
}
function letterPreview(job) {
  return `<strong>${esc(job.subject)}</strong>${esc(job.body)}`;
}
async function openLetter(id) {
  const job = await api(`/history/${id}`);
  drawerView(job.point_name || "Тестовое письмо", job.recipient, `<div class="row">${badge(...mailStates[job.status])}
      <span class="muted">${mailKinds[job.kind]} · ${dateTime(job.created_at)} МСК</span></div>
    ${job.error ? `<p class="error-box">${esc(job.error)}</p>` : ""}
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
        container.textContent = demo ? "Тест на демонстрационных данных завершён." : "Письмо принято SMTP. Проверьте входящие.";
        return;
      }
      if (job.status === "failed") {
        container.className = "error-box";
        container.textContent = job.error || "SMTP отклонил письмо.";
        return;
      }
    } catch {
      return;
    }
  }
  if (container.isConnected) container.textContent = "Письмо в очереди. Статус доступен в журнале.";
}
async function handleForm(form) {
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
    case "smtp-test": {
      const job = await api("/smtp/test", "POST", {email: values.get("email")});
      const result = form.querySelector("#test-result");
      result.className = "result-box";
      result.textContent = "Тестовое письмо в очереди.";
      trackTest(job.id, result);
      return;
    }
    case "monitor":
      await api("/monitor", "PUT", {enabled: values.has("enabled"), inclusive: values.has("inclusive"),
        poll_seconds: Number(values.get("poll_seconds")), delay_seconds: Number(values.get("delay_seconds"))});
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
  toast(result.checked ? `Проверено точек: ${result.checked}.` : "Нет активных точек с графиком на сегодня.");
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
      const result = await api(`/servers/${id}/probe`, "POST");
      toast(`iiko ответил. Смен за сегодня: ${result.shifts}.`);
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
  else rows.innerHTML = pointRows();
}
document.addEventListener("input", event => {
  if (event.target.id === "table-search") {
    ui.query = event.target.value;
    updateRows();
  }
});
document.addEventListener("change", event => {
  const node = event.target;
  if (node.id === "table-filter") {
    ui.filter = node.value;
    updateRows();
    content.querySelectorAll(".metric").forEach(item => item.setAttribute("aria-pressed", String(item.dataset.id === ui.filter)));
  } else if (node.hasAttribute("data-disable-time")) {
    node.closest(".schedule-row, .exception-row").querySelector("input[type=time]").disabled = node.checked;
  } else if (node.id === "department-choice" && node.value) {
    node.closest("form").elements.department_id.value = node.value;
  } else if (node.name === "server" && node.closest("[data-form=point]")) {
    node.closest("form").querySelector("#departments").replaceChildren();
  } else if (node.name === "security" && node.closest("[data-form=smtp]")) {
    const port = node.closest("form").elements.port;
    if (["465", "587"].includes(port.value)) port.value = node.value === "ssl" ? "465" : "587";
  }
});
document.querySelector("#mobile-nav").addEventListener("click", event => {
  const isOpen = document.querySelector("#sidebar").classList.toggle("open");
  event.currentTarget.setAttribute("aria-expanded", String(isOpen));
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
