"use strict";

const workSources = {knowledge: "knowledge", inventory: "inventory", admin: "employees", account: "profile"};
const workClock = new Intl.DateTimeFormat("ru-RU", {timeZone:"Europe/Moscow", dateStyle:"short", timeStyle:"short"});
const work = {page: "", data: null, error: "", selection: {}, article: null, asset: null, person: null, assigned: [], request: 0, collapsed: new Set(), serviceFilter: "all", statusFilter: "all"};

function isWorkPage(page) {
  return Object.hasOwn(workSources, page);
}

async function loadWork(reload = true, focusDetail = false) {
  const page = ui.page;
  if (!isWorkPage(page)) return;
  const request = ++work.request;
  try {
    const data = !reload && work.page === page && work.data ? work.data : await api(`/work/${workSources[page]}`);
    const item = data.items?.find(row => row.id === work.selection[page]) || data.items?.[0];
    let article = null, asset = null, person = null, assigned = [];
    if (page === "account") {
      ({person, assigned} = data);
    } else if (item) {
      if (page === "knowledge") article = await api(`/work/knowledge/${item.id}`);
      if (page === "inventory") asset = await api(`/work/inventory/${item.id}`);
      if (page === "admin") {
        person = item;
        assigned = (await api(`/work/inventory?employee=${encodeURIComponent(person.id)}`)).items;
      }
    }
    if (ui.page !== page || work.request !== request) return;
    if (article) revealArticle(article, data.items);
    if (item) work.selection[page] = item.id;
    Object.assign(work, {page, data, article, asset, person, assigned, error: ""});
  } catch (error) {
    if (ui.page !== page || work.request !== request) return;
    Object.assign(work, {page, data: null, article: null, asset: null, person: null, assigned: [], error: error.message});
  }
  render();
  const searchBox = content.querySelector("[data-work-search], [data-article-search]");
  if (searchBox) applyWorkSearch(searchBox);
  if (focusDetail) focusRecord();
}

function selectRecord(page, id) {
  const hash = `#${page}/${id}`;
  if (location.hash === hash) focusRecord();
  else location.hash = hash;
}

function workPage() {
  if (work.page !== ui.page) return `<p class="muted" role="status">Загрузка…</p>`;
  if (work.error) return empty("alert", "Раздел не загрузился", work.error, button("work-reload", "Повторить"));
  return {knowledge: knowledgePage, inventory: inventoryPage, admin: employeePage, account: accountPage}[ui.page]();
}

function articleHierarchy(items) {
  const children = new Map();
  for (const item of items) {
    if (!children.has(item.parent)) children.set(item.parent, []);
    children.get(item.parent).push(item);
  }
  return children;
}

function articleTree(children, parent = null, prefix = "") {
  const items = children.get(parent) || [];
  if (!items.length) return "";
  const attributes = parent ? `id="kb-children-${esc(parent)}" ${work.collapsed.has(parent) ? "hidden" : ""}` : "";
  return `<ol class="kb-tree" ${attributes}>${items.map((item, index) => {
    const number = prefix + (index + 1);
    const active = item.id === work.selection.knowledge ? 'aria-current="true"' : "";
    const toggle = children.has(item.id) ?
      `<button type="button" class="kb-toggle" data-action="work-toggle-branch" data-id="${esc(item.id)}"
        aria-label="Подпункты · ${esc(item.title)}" aria-controls="kb-children-${esc(item.id)}"
        aria-expanded="${!work.collapsed.has(item.id)}">${icon("right")}</button>` : "";
    return `<li data-knowledge-node="${esc(item.id)}" data-title="${esc(item.title)}"><div class="kb-node">
      ${toggle}<button type="button" class="kb-node-name" data-action="work-open-article" data-id="${esc(item.id)}" ${active}>
      <span class="muted tabular">${number}</span> ${icon(item.kind === "section" ? "folder" : "list")}${esc(item.title)}</button></div>
      ${articleTree(children, item.id, number + ".")}</li>`;
  }).join("")}</ol>`;
}

function toggleBranch(id, button) {
  const branch = document.getElementById(button.getAttribute("aria-controls"));
  const collapsed = !branch.hidden;
  if (collapsed) work.collapsed.add(id);
  else work.collapsed.delete(id);
  branch.hidden = collapsed;
  button.setAttribute("aria-expanded", String(!collapsed));
}

function revealArticle(item, items) {
  let parent = item.parent;
  const parents = new Map(items.map(row => [row.id, row.parent]));
  while (parent) {
    work.collapsed.delete(parent);
    parent = parents.get(parent);
  }
}

function knowledgePage() {
  const writer = grants.has("knowledge.write");
  const actions = writer ? button("work-import", "Импорт документа", {icon:"list"}) +
    button("work-new-section", "Новый раздел", {icon:"folder"}) +
    button("work-new-article", "Новая статья", {icon:"plus", kind:"primary"}) : "";
  const top = pageHead(labels.knowledge, "", actions);
  if (!work.article) return top + `<section class="card">${empty("book", "База знаний пока пуста")}</section>`;
  const item = work.article;
  const children = articleHierarchy(work.data.items);
  const entries = children.get(item.id) || [];
  const tools = writer ? button("work-child", "Добавить статью", {id:item.id, icon:"plus"}) +
    button("work-edit-article", "Редактировать", {icon:"pencil"}) : "";
  const section = entries.length ? `<ul class="kb-section-entries">${entries.map(child =>
    `<li><a href="#knowledge/${esc(child.id)}">${icon(child.kind === "section" ? "folder" : "list")}${esc(child.title)}</a></li>`).join("")}</ul>` :
    '<p class="muted">В этом разделе пока нет статей.</p>';
  return top + `<div class="work-split"><section class="card"><div class="card-head"><h2>Содержание</h2></div>
    <div class="card-body work-tree-search"><label class="search">${icon("search")}<input type="search" data-article-search aria-label="Найти пункт" placeholder="Найти пункт" value="${esc(ui.query)}"></label></div>
    <nav class="card-body" aria-label="Пункты базы знаний">${articleTree(children)}<p data-no-articles class="muted" hidden>Ничего не найдено.</p></nav></section>
    <article class="card record-detail" tabindex="-1"><div class="card-head"><h2>${esc(item.title)}</h2><div class="row">${tools}</div></div>
    <div class="card-body stack">${item.kind === "section" ? section : `<div class="kb-document">${articleContents(item)}</div>`}
    ${item.filename ? `<a class="button work-file" href="/api/work/knowledge/${esc(item.id)}/original" download>${icon("list")}Скачать ${esc(item.filename)}</a>` : ""}
    <p class="muted">Обновлено ${esc(workClock.format(new Date(item.edited_at)))} · ${esc(item.edited_by)}</p></div></article></div>`;
}

function parentOptions(parent, exclude) {
  const children = articleHierarchy(work.data.items);
  const options = ['<option value="">Верхний уровень</option>'];
  function visit(id, path) {
    for (const item of children.get(id) || []) {
      if (item.id === exclude) continue;
      const full = [...path, item.title];
      options.push(`<option value="${esc(item.id)}" ${selected(item.id === parent)}>${esc(full.join(" / "))}</option>`);
      visit(item.id, full);
    }
  }
  visit(null, []);
  return options.join("");
}

function articleForm(item, parent, kind = item?.kind || "article") {
  const section = kind === "section";
  const title = item ? (section ? "Редактировать раздел" : "Редактировать статью") : (section ? "Новый раздел" : "Новая статья");
  drawerView(title, "", `
    <input type="hidden" name="kind" value="${kind}">
    <div class="field-grid"><label>Название<input name="title" maxlength="240" required value="${esc(item?.title)}"></label>
    <label>Раздел<select name="parent" aria-label="Родительский пункт">${parentOptions(parent, item?.id)}</select></label></div>
    ${section ? "" : knowledgeEditorBody(item)}
    <input type="hidden" name="version" value="${esc(item?.version)}">`,
  {form:"work-article", id:item?.id, submit:"Сохранить", wide:!section});
  if (!section) attachArticleEditor(drawer.querySelector("form"));
}

function importForm() {
  drawerView("Импорт документа", "", `
    <label>Родительский пункт<select name="parent" aria-label="Родительский пункт">${parentOptions(work.article?.id)}</select></label>
    <label>Документ<input type="file" name="file" accept=".docx,.md,.txt,.pdf" required>
    <small class="hint">DOCX, Markdown, TXT или PDF до 5 МБ. Для сканов сохраняется оригинал.</small></label>`,
    {form:"work-import", submit:"Импортировать"});
}

const serviceBadges = {
  unset: ["Срок не указан", ""], not_started: ["Не начат", ""],
  soon: ["Скоро истекает", "amber"], today: ["Истекает сегодня", "amber"], expired: ["Срок истёк", "red"],
};

function serviceCell(asset) {
  const until = asset.service_expires_on ? `<span class="tabular">${esc(reportDate(asset.service_expires_on))}</span>` : "";
  const details = serviceBadges[asset.service_status];
  const mark = details ? badge(...details) : "";
  return `<div class="service-cell">${until}${mark}</div>`;
}

function serviceField(months) {
  return `<label>Срок эксплуатации, мес.<input type="number" name="service_months" min="1" max="600" step="1"
    required value="${esc(months)}" placeholder="Например, 36"></label>`;
}

function assetFilters() {
  const days = work.data.service_warning_days;
  return `<select data-service-filter aria-label="Фильтр по сроку эксплуатации">
    ${[["all","Все сроки"], ["due",`Истекает в течение ${days} дней`], ["expired","Срок истёк"], ["unset","Срок не указан"]]
      .map(([value,label]) => `<option value="${value}" ${selected(value === work.serviceFilter)}>${label}</option>`).join("")}
    </select>`;
}

function assetStatusBadge(state, started) {
  if (!started) return badge("Принято", "blue");
  return badge(work.data.states[state], state === "in_use" ? "green" : "");
}

function assetStatusFilters(items) {
  const filters = [
    ["all", "Вся техника", items.length, ""],
    ["received", "Принято", items.filter(asset => !asset.service_started_on).length, "blue"],
    ["in_use", "В эксплуатации", items.filter(asset => asset.state === "in_use").length, "green"],
    ["stock", "На складе", items.filter(asset => asset.state === "stock").length, ""],
  ];
  return `<div class="metrics inventory-metrics" role="group" aria-label="Состояние техники">
    ${filters.map(([key, label, count, tone]) => `<button class="metric ${tone}" type="button" data-action="work-inventory-filter"
      data-id="${key}" aria-pressed="${work.statusFilter === key}">${label}<span class="tabular">${count}</span></button>`).join("")}</div>`;
}

function filterAssetStatus(id) {
  work.statusFilter = id;
  for (const control of content.querySelectorAll('[data-action="work-inventory-filter"]')) {
    control.setAttribute("aria-pressed", String(control.dataset.id === id));
  }
  applyWorkSearch(content.querySelector("[data-work-search]"));
}

function inventoryPage() {
  const writer = grants.has("inventory.write");
  const top = pageHead(labels.inventory, "", writer ? button("work-receive", "Принять технику", {icon:"plus", kind:"primary"}) : "");
  if (!work.asset) return top + `<section class="card">${empty("device", "В реестре пока нет техники")}</section>`;
  return top + assetStatusFilters(work.data.items) + `<div class="record-layout inventory-layout"><section class="card inventory-list"><div class="card-head">
    <h2>Реестр техники</h2><div class="table-controls">${workSearch("Номер, техника или сотрудник")}${assetFilters()}</div></div>
    <div class="table-wrap"><table class="point-table inventory-table"><thead><tr><th>Инв. номер</th><th>Техника</th>
    <th>Состояние</th><th>Сотрудник</th><th>Срок эксплуатации</th></tr></thead>
    <tbody>${work.data.items.map(asset => `<tr data-work-row data-service-status="${asset.service_status}" data-asset-state="${asset.state}"
      data-asset-received="${!asset.service_started_on}" data-action="work-asset"
      data-id="${esc(asset.id)}" class="${asset.id === work.asset.id ? "selected-row" : ""}">
      <td><button type="button" class="point-button" data-action="work-asset" data-id="${esc(asset.id)}" aria-pressed="${asset.id === work.asset.id}">${esc(asset.tag)}</button></td>
      <td data-label="Техника">${esc(asset.name)}</td><td data-label="Состояние">${assetStatusBadge(asset.state, asset.service_started_on)}</td>
      <td data-label="Сотрудник">${esc(asset.employee_name || "—")}</td><td data-label="Срок эксплуатации">${serviceCell(asset)}</td></tr>`).join("")}</tbody></table>
    <p class="card-body muted" data-inventory-empty hidden>Техника по выбранным фильтрам не найдена.</p></div></section>${assetCard(work.asset)}</div>`;
}

function workSearch(label) {
  return `<label class="search">${icon("search")}<input type="search" data-work-search aria-label="${esc(label)}" placeholder="${esc(label)}" value="${esc(ui.query)}"></label>`;
}

function receiveForm() {
  drawerView("Приёмка техники", "", `
    <label>Инвентарный номер<input name="tag" required maxlength="100"></label>
    <label>Наименование<input name="name" required maxlength="160"></label>
    <label>Серийный номер<input name="serial" maxlength="120"></label>
    <label>Дата приёмки<input type="date" name="day" required value="${work.data.today}" max="${work.data.today}"></label>
    <label>Место приёмки<input name="location" maxlength="160" required value="Склад"></label>
    <label>Фото устройства <small>необязательно</small><input type="file" name="photo" accept="${photoInputTypes}"></label>
    <label>Заметка или реквизиты документа<input name="note" maxlength="500"></label>`, {form:"work-receive", submit:"Принять на склад"});
}

function assetCard(item) {
  const last = item.events[0];
  const received = item.events[item.events.length - 1];
  const actions = work.data.actions;
  const history = item.events.map((event, index) => {
    const previous = item.events[index + 1];
    const place = esc(event.location || "Место не указано");
    const origin = esc(previous?.location || "Место не указано");
    const route = previous && previous.location !== event.location ? `${origin} → ${place}` : place;
    return `<li><div class="asset-event-head"><strong>${esc(actions[event.action])}</strong><time datetime="${event.day}">${esc(reportDate(event.day))}</time></div>
      <span class="asset-event-route">${route}</span>
      ${event.employee_name ? `<span>Ответственный · ${esc(event.employee_name)}</span>` : ""}
      <small>Оформил ${esc(event.actor_name)} · ${esc(workClock.format(new Date(event.recorded_at)))} МСК</small>
      ${event.note ? `<p>${esc(event.note)}</p>` : ""}</li>`;
  }).join("");
  const moves = last.state === "stock" ? ["assign","commission"] : last.employee ? ["transfer","return"] : ["assign","return"];
  const controls = grants.has("inventory.write") ? moves.map((action, index) =>
    button("work-move", actions[action], {id:`${item.id}/${action}`, kind:index === 0 ? "primary" : ""})).join("") : "";
  const writer = grants.has("inventory.write");
  const owner = last.employee && grants.has("admin.manage")
    ? `<a href="#admin/${esc(last.employee)}">${esc(item.employee_name)}</a>` : esc(item.employee_name || "—");
  const dateStarted = item.service_started_on ? esc(reportDate(item.service_started_on)) : "—";
  const photoURL = `/api/work/inventory/${esc(item.id)}/photo`;
  const photo = item.has_photo ? `<a class="asset-photo" href="${photoURL}" target="_blank" rel="noopener">
    <img src="${photoURL}" alt="Фото · ${esc(item.name)}"></a>` : "";
  const relocate = writer ? button("work-move", "Переместить", {id:`${item.id}/relocate`}) : "";
  const photoButton = writer ? button("work-photo", item.has_photo ? "Заменить фото" : "Добавить фото", {icon:"image"}) : "";
  const termButton = writer && item.service_started_on ?
    button("work-service", item.service_months ? "Изменить срок" : "Указать срок", {icon:"clock"}) : "";
  return `<section class="card record-detail" aria-label="Карточка техники" tabindex="-1"><div class="card-head"><h2>${esc(item.name)}</h2>${listButton()}</div>
    <div class="card-body stack"><div class="row"><strong>${esc(item.tag)}</strong>${assetStatusBadge(last.state, item.service_started_on)}</div>
    ${photo}<dl class="profile-facts"><div><dt>Дата приёмки</dt><dd>${esc(reportDate(received.day))}</dd></div>
    <div><dt>Серийный номер</dt><dd>${esc(item.serial || "—")}</dd></div><div><dt>Закреплено за</dt><dd>${owner}</dd></div>
    <div><dt>Текущее место</dt><dd>${esc(last.location || "Место не указано")}</dd></div>
    <div><dt>Начало эксплуатации</dt><dd>${dateStarted}</dd></div>
    <div><dt>Срок эксплуатации</dt><dd>${item.service_months ? esc(item.service_months) + " мес." : "—"}</dd></div>
    <div><dt>Окончание срока</dt><dd>${item.service_started_on ? serviceCell(item) : "—"}</dd></div></dl>
    ${writer ? `<div class="row">${controls}${relocate}${termButton}${photoButton}</div>` : ""}
    <section aria-label="История устройства"><h3>История устройства</h3><ol class="movement-list">${history}</ol></section>
    </div></section>`;
}

async function movementForm(key) {
  const [id, action] = key.split("/");
  const assign = ["assign","transfer"].includes(action);
  const [item, staff] = await Promise.all([api(`/work/inventory/${id}`), assign ? api("/work/inventory/assignees") : Promise.resolve({items:[]})]);
  if (ui.page !== "inventory") return;
  const last = item.events[0];
  const choices = staff.items.filter(person => person.id !== last.employee);
  const selectEmployee = assign ? `<label>Сотрудник<select name="employee" aria-label="Сотрудник" required><option value="">Выберите сотрудника</option>
    ${choices.map(person => `<option value="${esc(person.id)}">${esc(person.name)}</option>`).join("")}</select></label>
    ${choices.length ? "" : '<p class="notice">Для выдачи нужен действующий сотрудник. Его добавляет администратор.</p>'}` : "";
  const relocating = action === "relocate";
  const destination = relocating ? "" : action === "return" ? "Склад" : last.location || "";
  drawerView(work.data.actions[action], `${item.tag} · ${item.name}`, `
    <input type="hidden" name="action" value="${esc(action)}"><input type="hidden" name="version" value="${item.version}">
    ${selectEmployee}${!item.service_started_on && ["assign","commission"].includes(action) ? serviceField() : ""}
    ${relocating ? `<p class="muted">Текущее место · ${esc(last.location || "Место не указано")}</p>` : ""}
    <label>${relocating ? "Куда переместить" : "Место устройства"}<input name="location" maxlength="160"
      value="${esc(destination)}" placeholder="Например, склад или название точки" ${relocating ? "required" : ""}></label>
    <label>Дата операции<input name="day" type="date" required min="${last.day}" max="${work.data.today}" value="${work.data.today}"></label>
    <label>Заметка или реквизиты документа<input name="note" maxlength="500"></label>`, {form:"work-move", id, submit:"Провести операцию"});
}

function serviceForm() {
  const item = work.asset;
  drawerView("Срок эксплуатации", item.name, `
    <p class="muted">Первый ввод в эксплуатацию · ${esc(reportDate(item.service_started_on))}</p>
    <input type="hidden" name="service_revision" value="${item.service_revision}">
    ${serviceField(item.service_months)}`, {form:"work-service", id:item.id, submit:"Сохранить"});
}

function photoForm() {
  const item = work.asset;
  drawerView("Фото устройства", item.name, `
    <label>Фото<input type="file" name="photo" accept="${photoInputTypes}" ${item.has_photo ? "" : "required"}></label>
    <p class="hint">JPG, PNG или WebP до 5 МБ.</p>
    ${item.has_photo ? '<label class="check"><input type="checkbox" name="remove">Удалить текущее фото</label>' : ""}
    `, {form:"work-photo", id:item.id, submit:"Сохранить"});
}

function employeePage() {
  const top = pageHead("Сотрудники и доступ", "", button("work-employee", "Добавить сотрудника", {kind:"primary",icon:"plus"}));
  if (!work.person) return top + `<section class="card">${empty("users", "Сотрудники пока не добавлены")}</section>`;
  return top + `<div class="record-layout"><section class="card"><div class="card-head"><h2>Список сотрудников</h2>${workSearch("Имя или логин")}</div>
    <div class="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Логин</th><th>Доступ</th></tr></thead>
    <tbody>${work.data.items.map(person => `<tr data-work-row data-action="work-select-employee" data-id="${esc(person.id)}" class="${person.id === work.person.id ? "selected-row" : ""}">
      <td><button type="button" class="point-button" data-action="work-select-employee" data-id="${esc(person.id)}" aria-pressed="${person.id === work.person.id}">${esc(person.name)}</button></td>
      <td>${esc(person.login || "—")}</td><td>${!person.active ? badge("Архив", "") :
        person.login_enabled ? badge("Есть доступ", "green") : badge("Только учёт", "blue")}</td></tr>`).join("")}</tbody></table></div></section>${profileCard(false)}</div>`;
}

function profileCard(own) {
  const person = work.person;
  const initials = person.name.trim().split(/\s+/).slice(0, 2).map(word => word[0]).join("").toLocaleUpperCase("ru");
  const rights = person.grants.map(key => work.data.permissions[key]).filter(Boolean);
  const showAssets = own ? grants.has("inventory.read") : true;
  const assets = work.assigned.map(item => `<a href="#inventory/${esc(item.id)}">${icon("device")}${esc(item.tag)} · ${esc(item.name)}</a>`).join("");
  const account = person.id === "@owner" ? "Администратор установки" : !person.login_enabled ? "Только учёт" :
    person.must_change ? "Требуется смена пароля" : "Личный пароль задан";
  const actions = own ? document.querySelector("#account-actions").innerHTML :
    button("work-employee", "Редактировать доступ", {id:person.id,icon:"pencil"});
  return `<section class="card record-detail" aria-label="Карточка сотрудника" tabindex="-1"><div class="card-head"><h2>${esc(person.name)}</h2>${own ? "" : listButton()}</div>
    <div class="card-body stack"><div class="profile-identity"><span class="profile-avatar" aria-hidden="true">${esc(initials)}</span>
    <div><strong>${person.grants.includes("admin.manage") ? "Администратор" : "Сотрудник"}</strong>${person.login ? `<small>${esc(person.login)}</small>` : ""}</div></div>
    <dl class="profile-facts"><div><dt>Авторизация</dt><dd>${account}</dd></div><div><dt>Вход</dt><dd>${person.active && person.login_enabled ? "Разрешён" : "Закрыт"}</dd></div>
    <div><dt>Права</dt><dd>${rights.length ? rights.map(esc).join("<br>") : "—"}</dd></div></dl>
    <div class="row">${actions}</div>${showAssets ? `<section class="profile-assets"><h3>Закреплённая техника</h3>${assets || '<p class="muted">Техника не закреплена.</p>'}</section>` : ""}
    </div></section>`;
}

function accountPage() {
  return pageHead("Мой профиль", "") + `<div class="account-panel">${profileCard(true)}</div>`;
}

function employeeForm(id) {
  const person = work.data.items.find(item => item.id === id);
  const enabled = Boolean(person?.login_enabled);
  const rights = Object.entries(work.data.permissions).map(([key, label]) =>
    `<label class="check"><input type="checkbox" name="grants" value="${esc(key)}" ${checked(person?.grants.includes(key))}>${esc(label)}</label>`).join("");
  drawerView(person ? "Сотрудник" : "Новый сотрудник", "", `
    <label>Имя сотрудника<input name="name" maxlength="160" value="${esc(person?.name)}" required></label>
    <input type="hidden" name="version" value="${esc(person?.version)}">
    <label class="check"><input name="active" type="checkbox" ${checked(person?.active !== false)}>Сотрудник активен</label>
    <label class="check"><input name="login_enabled" type="checkbox" ${checked(enabled)}>Доступ в панель</label>
    <fieldset class="work-rights stack" data-access-fields ${enabled ? "" : "hidden disabled"}>
      <label>Логин<input name="login" maxlength="120" value="${esc(person?.login)}" autocomplete="off" required></label>
      <label>${enabled ? "Новый временный пароль" : "Временный пароль"}<input type="password" name="password"
        minlength="12" maxlength="256" autocomplete="new-password" ${enabled ? "" : "required"}>
        <small class="hint">${enabled ? "Заполните для сброса пароля. " : ""}При первом входе сотрудник задаст свой пароль.</small></label>
      <fieldset class="work-rights"><legend>Права доступа</legend><div class="list-checks">${rights}</div></fieldset>
    </fieldset>`, {form:"work-employee", id, submit:"Сохранить"});
}

async function submitWork(form) {
  const page = ui.page;
  const values = new FormData(form);
  const data = Object.fromEntries(values);
  if (data.version) data.version = Number(data.version);
  const id = form.dataset.id;
  let result;
  switch (form.dataset.form) {
    case "work-article":
      result = await api(id ? `/work/knowledge/${id}` : "/work/knowledge", id ? "PUT" : "POST", articlePayload(form, data));
      break;
    case "work-import":
      result = await api("/work/knowledge/import", "POST", values);
      break;
    case "work-employee":
      data.active = values.has("active");
      data.login_enabled = values.has("login_enabled");
      data.grants = values.getAll("grants");
      result = await api(id ? `/work/employees/${id}` : "/work/employees", id ? "PUT" : "POST", data);
      break;
    case "work-receive":
      result = await api("/work/inventory", "POST", values);
      break;
    case "work-move":
      if (values.has("service_months")) data.service_months = Number(data.service_months);
      result = await api(`/work/inventory/${id}/events`, "POST", data);
      break;
    case "work-service":
      result = await api(`/work/inventory/${id}/service`, "PUT",
                         {service_months:Number(data.service_months), service_revision:Number(data.service_revision)});
      break;
    case "work-photo":
      result = await api(`/work/inventory/${id}/photo`, values.has("remove") ? "DELETE" : "PUT", values);
      break;
  }
  if (ui.page !== page || !drawer.open || !drawer.contains(form)) return;
  if (result) {
    ui.query = "";
    work.selection[ui.page] = result.id;
    history.replaceState(null, "", `#${ui.page}/${result.id}`);
  }
  drawer.close();
  await loadWork(true, Boolean(result));
  toast("Сохранено.");
}

const workActions = {
  "work-reload": loadWork,
  "work-open-article": id => { location.hash = `knowledge/${id}`; },
  "work-new-section": () => articleForm(null, null, "section"),
  "work-new-article": () => articleForm(null, work.article?.kind === "section" ? work.article.id : null),
  "work-toggle-branch": toggleBranch,
  "work-child": id => articleForm(null, id),
  "work-edit-article": () => articleForm(work.article, work.article.parent),
  "work-import": importForm,
  "work-receive": receiveForm,
  "work-asset": id => selectRecord("inventory", id),
  "work-inventory-filter": filterAssetStatus,
  "work-select-employee": id => selectRecord("admin", id),
  "work-editor": () => work.article?.kind === "section" ? articleForm(null, work.article.id) :
    articleForm(work.article, work.article?.parent),
  "work-edit-selected": () => employeeForm(work.person?.id),
  "work-move": movementForm,
  "work-service": serviceForm,
  "work-photo": photoForm,
  "work-employee": employeeForm,
};

document.addEventListener("input", event => {
  if (!event.target.matches("[data-work-search], [data-article-search]")) return;
  ui.query = event.target.value;
  applyWorkSearch(event.target);
});

function applyWorkSearch(input) {
  const query = input.value.trim().toLocaleLowerCase("ru");
  if (input.hasAttribute("data-article-search")) {
    const tree = input.closest(".card");
    const nodes = [...tree.querySelectorAll("[data-knowledge-node]")];
    for (const node of nodes.reverse()) {
      const branch = node.querySelector(":scope > ol");
      const visibleChild = branch?.querySelector(":scope > li:not([hidden])");
      node.hidden = Boolean(query && !node.dataset.title.toLocaleLowerCase("ru").includes(query) && !visibleChild);
      if (branch) {
        branch.hidden = query ? !visibleChild : work.collapsed.has(node.dataset.knowledgeNode);
        node.querySelector(":scope > .kb-node .kb-toggle").setAttribute("aria-expanded", String(!branch.hidden));
      }
    }
    tree.querySelector("[data-no-articles]").hidden = nodes.some(node => !node.hidden);
    return;
  }
  const list = input.closest(".card");
  const rows = [...list.querySelectorAll("[data-work-row]")];
  for (const row of rows) {
    const status = row.dataset.serviceStatus;
    const filter = work.serviceFilter;
    const termMatches = !status || filter === "all" || filter === status || filter === "due" && ["today","soon"].includes(status);
    const state = row.dataset.assetState;
    const stateMatches = !state || work.statusFilter === "all" || state === work.statusFilter ||
      work.statusFilter === "received" && row.dataset.assetReceived === "true";
    row.hidden = !termMatches || !stateMatches || !row.textContent.toLocaleLowerCase("ru").includes(query);
  }
  const emptyState = list.querySelector("[data-inventory-empty]");
  if (emptyState) emptyState.hidden = rows.some(row => !row.hidden);
}

document.addEventListener("change", event => {
  const input = event.target;
  if (input.hasAttribute("data-service-filter")) {
    work.serviceFilter = input.value;
    applyWorkSearch(input.closest(".card").querySelector("[data-work-search]"));
    return;
  }
  const form = input.closest('[data-form="work-employee"]');
  if (!form) return;
  if (input.name === "login_enabled") {
    const access = form.querySelector("[data-access-fields]");
    access.hidden = access.disabled = !input.checked;
    return;
  }
  if (input.name !== "grants") return;
  const options = [...input.form.querySelectorAll('[name="grants"]')];
  if (input.value === "admin.manage" && input.checked) {
    options.forEach(option => { option.checked = true; });
    return;
  }
  if (!input.checked) options.find(option => option.value === "admin.manage").checked = false;
  const [module, level] = input.value.split(".");
  const dependency = level === "write" && input.checked ? "read" : level === "read" && !input.checked ? "write" : null;
  if (dependency) options.find(option => option.value === `${module}.${dependency}`).checked = input.checked;
});
