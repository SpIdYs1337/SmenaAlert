"use strict";

const workSources = {knowledge: "knowledge", inventory: "inventory", admin: "employees", account: "profile"};
const workClock = new Intl.DateTimeFormat("ru-RU", {timeZone:"Europe/Moscow", dateStyle:"short", timeStyle:"short"});
const work = {page: "", data: null, error: "", selection: {}, article: null, asset: null, person: null, assigned: [], request: 0};

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
    if (item) work.selection[page] = item.id;
    Object.assign(work, {page, data, article, asset, person, assigned, error: ""});
  } catch (error) {
    if (ui.page !== page || work.request !== request) return;
    Object.assign(work, {page, data: null, article: null, asset: null, person: null, assigned: [], error: error.message});
  }
  render();
  const searchBox = content.querySelector("[data-work-search], [data-article-search]");
  if (searchBox && ui.query) applyWorkSearch(searchBox);
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
  return `<ol class="kb-tree">${items.map((item, index) => {
    const number = prefix + (index + 1);
    const active = item.id === work.selection.knowledge ? 'aria-current="true"' : "";
    return `<li><button type="button" data-action="work-open-article" data-id="${esc(item.id)}" ${active}>
      <span class="muted tabular">${number}</span> ${esc(item.title)}</button>${articleTree(children, item.id, number + ".")}</li>`;
  }).join("")}</ol>`;
}

function knowledgePage() {
  const writer = grants.has("knowledge.write");
  const actions = writer ? button("work-import", "Импорт документа", {icon:"list"}) +
    button("work-new-article", "Новый пункт", {icon:"plus", kind:"primary"}) : "";
  const top = pageHead(labels.knowledge, "", actions);
  if (!work.article) return top + `<section class="card">${empty("book", "База знаний пока пуста")}</section>`;
  const item = work.article;
  const tools = writer ? button("work-child", "Добавить подпункт", {id:item.id, icon:"plus"}) +
    button("work-edit-article", "Редактировать", {icon:"pencil"}) : "";
  return top + `<div class="work-split"><section class="card"><div class="card-head"><h2>Содержание</h2></div>
    <div class="card-body work-tree-search"><label class="search">${icon("search")}<input type="search" data-article-search aria-label="Найти пункт" placeholder="Найти пункт" value="${esc(ui.query)}"></label></div>
    <nav class="card-body" aria-label="Пункты базы знаний">${articleTree(articleHierarchy(work.data.items))}</nav></section>
    <article class="card"><div class="card-head"><h2>${esc(item.title)}</h2><div class="row">${tools}</div></div>
    <div class="card-body stack"><div class="kb-body">${esc(item.body)}</div>
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

function articleForm(item, parent) {
  drawerView(item ? "Редактировать пункт" : parent ? "Добавить подпункт" : "Новый пункт", "", `
    <label>Родительский пункт<select name="parent" aria-label="Родительский пункт">${parentOptions(parent, item?.id)}</select></label>
    <label>Название пункта<input name="title" maxlength="240" required value="${esc(item?.title)}"></label>
    <label>Текст<textarea aria-label="Текст" name="body" rows="12" maxlength="180000">${esc(item?.body)}</textarea></label>
    <input type="hidden" name="version" value="${esc(item?.version)}">`, {form:"work-article", id:item?.id, submit:"Сохранить"});
}

function importForm() {
  drawerView("Импорт документа", "", `
    <label>Родительский пункт<select name="parent" aria-label="Родительский пункт">${parentOptions(work.article?.id)}</select></label>
    <label>Документ<input type="file" name="file" accept=".docx,.md,.txt,.pdf" required>
    <small class="hint">DOCX, Markdown, TXT или PDF до 5 МБ. Для сканов сохраняется оригинал.</small></label>`,
    {form:"work-import", submit:"Импортировать"});
}

function inventoryPage() {
  const writer = grants.has("inventory.write");
  const top = pageHead(labels.inventory, "", writer ? button("work-receive", "Принять технику", {icon:"plus", kind:"primary"}) : "");
  if (!work.asset) return top + `<section class="card">${empty("device", "В реестре пока нет техники")}</section>`;
  return top + `<div class="record-layout"><section class="card"><div class="card-head"><h2>Реестр техники</h2>${workSearch("Номер, техника или сотрудник")}</div>
    <div class="table-wrap"><table><thead><tr><th>Инв. номер</th><th>Техника</th><th>Состояние</th><th>Сотрудник</th></tr></thead>
    <tbody>${work.data.items.map(asset => `<tr data-work-row data-action="work-asset" data-id="${esc(asset.id)}" class="${asset.id === work.asset.id ? "selected-row" : ""}">
      <td><button type="button" class="point-button" data-action="work-asset" data-id="${esc(asset.id)}" aria-pressed="${asset.id === work.asset.id}">${esc(asset.tag)}</button></td>
      <td>${esc(asset.name)}</td><td>${badge(work.data.states[asset.state], asset.state === "in_use" ? "green" : "blue")}</td>
      <td>${esc(asset.employee_name || "—")}</td></tr>`).join("")}</tbody></table></div></section>${assetCard(work.asset)}</div>`;
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
    <label>Документ или комментарий<input name="note" maxlength="500"></label>`, {form:"work-receive", submit:"Принять на склад"});
}

function assetCard(item) {
  const last = item.events[0];
  const received = item.events[item.events.length - 1];
  const actions = work.data.actions;
  const history = item.events.map(event => `<li><strong>${esc(actions[event.action])}</strong>
    <span>${esc(reportDate(event.day))}${event.employee_name ? " · " + esc(event.employee_name) : ""}</span>
    <small>Оформил ${esc(event.actor_name)} · ${esc(workClock.format(new Date(event.recorded_at)))} МСК</small>
    ${event.note ? `<p>${esc(event.note)}</p>` : ""}</li>`).join("");
  const moves = last.state === "stock" ? ["assign","commission"] : last.employee ? ["transfer","return"] : ["assign","return"];
  const controls = grants.has("inventory.write") ? moves.map((action, index) =>
    button("work-move", actions[action], {id:`${item.id}/${action}`, kind:index === 0 ? "primary" : ""})).join("") : "";
  const owner = last.employee && grants.has("admin.manage")
    ? `<a href="#admin/${esc(last.employee)}">${esc(last.employee_name)}</a>` : esc(last.employee_name || "—");
  return `<section class="card record-detail" aria-label="Карточка техники" tabindex="-1"><div class="card-head"><h2>${esc(item.name)}</h2>${listButton()}</div>
    <div class="card-body stack"><div class="row"><strong>${esc(item.tag)}</strong>${badge(work.data.states[last.state], last.state === "stock" ? "blue" : "green")}</div>
    <dl class="profile-facts"><div><dt>Принято</dt><dd>${esc(reportDate(received.day))}</dd></div>
    <div><dt>Серийный номер</dt><dd>${esc(item.serial || "—")}</dd></div><div><dt>Закреплено за</dt><dd>${owner}</dd></div></dl>
    ${controls ? `<div class="row">${controls}</div>` : ""}<section><h3>Журнал движения</h3><ol class="movement-list">${history}</ol></section>
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
  drawerView(work.data.actions[action], `${item.tag} · ${item.name}`, `
    <input type="hidden" name="action" value="${esc(action)}"><input type="hidden" name="version" value="${item.version}">
    ${selectEmployee}<label>Дата операции<input name="day" type="date" required min="${last.day}" max="${work.data.today}" value="${work.data.today}"></label>
    <label>Документ или комментарий<input name="note" maxlength="500"></label>`, {form:"work-move", id, submit:"Провести операцию"});
}

function employeePage() {
  const top = pageHead("Сотрудники и доступ", "", button("work-employee", "Добавить сотрудника", {kind:"primary",icon:"plus"}));
  if (!work.person) return top + `<section class="card">${empty("users", "Сотрудники пока не добавлены")}</section>`;
  return top + `<div class="record-layout"><section class="card"><div class="card-head"><h2>Список сотрудников</h2>${workSearch("Имя или логин")}</div>
    <div class="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Логин</th><th>Доступ</th></tr></thead>
    <tbody>${work.data.items.map(person => `<tr data-work-row data-action="work-select-employee" data-id="${esc(person.id)}" class="${person.id === work.person.id ? "selected-row" : ""}">
      <td><button type="button" class="point-button" data-action="work-select-employee" data-id="${esc(person.id)}" aria-pressed="${person.id === work.person.id}">${esc(person.name)}</button></td>
      <td>${esc(person.login)}</td><td>${badge(person.active ? "Активен" : "Заблокирован", person.active ? "green" : "red")}</td></tr>`).join("")}</tbody></table></div></section>${profileCard(false)}</div>`;
}

function profileCard(own) {
  const person = work.person;
  const initials = person.name.trim().split(/\s+/).slice(0, 2).map(word => word[0]).join("").toLocaleUpperCase("ru");
  const rights = person.grants.map(key => work.data.permissions[key]).filter(Boolean);
  const showAssets = own ? grants.has("inventory.read") : true;
  const assets = work.assigned.map(item => `<a href="#inventory/${esc(item.id)}">${icon("device")}${esc(item.tag)} · ${esc(item.name)}</a>`).join("");
  const account = person.id === "@owner" ? "Администратор установки" : person.must_change ? "Требуется смена пароля" : "Личный пароль задан";
  const actions = own ? document.querySelector("#account-actions").innerHTML :
    button("work-employee", "Редактировать доступ", {id:person.id,icon:"pencil"});
  return `<section class="card record-detail" aria-label="Карточка сотрудника" tabindex="-1"><div class="card-head"><h2>${esc(person.name)}</h2>${own ? "" : listButton()}</div>
    <div class="card-body stack"><div class="profile-identity"><span class="profile-avatar" aria-hidden="true">${esc(initials)}</span>
    <div><strong>${person.grants.includes("admin.manage") ? "Администратор" : "Сотрудник"}</strong><small>${esc(person.login)}</small></div></div>
    <dl class="profile-facts"><div><dt>Авторизация</dt><dd>${account}</dd></div><div><dt>Вход</dt><dd>${person.active ? "Разрешён" : "Заблокирован"}</dd></div>
    <div><dt>Права</dt><dd>${rights.length ? rights.map(esc).join("<br>") : "Доступ к разделам пока не назначен"}</dd></div></dl>
    <div class="row">${actions}</div>${showAssets ? `<section class="profile-assets"><h3>Закреплённая техника</h3>${assets || '<p class="muted">Техника не закреплена.</p>'}</section>` : ""}
    </div></section>`;
}

function accountPage() {
  return pageHead("Мой профиль", "") + `<div class="account-panel">${profileCard(true)}</div>`;
}

function employeeForm(id) {
  const person = work.data.items.find(item => item.id === id);
  const rights = Object.entries(work.data.permissions).map(([key, label]) =>
    `<label class="check"><input type="checkbox" name="grants" value="${esc(key)}" ${checked(person?.grants.includes(key))}>${esc(label)}</label>`).join("");
  drawerView(person ? "Сотрудник" : "Новый сотрудник", "", `
    <label>Имя сотрудника<input name="name" maxlength="160" value="${esc(person?.name)}" required></label>
    <label>Логин<input name="login" maxlength="120" value="${esc(person?.login)}" autocomplete="off" required></label>
    <label>${person ? "Новый временный пароль" : "Временный пароль"}<input type="password" name="password" minlength="12" maxlength="256"
      autocomplete="new-password" ${person ? "" : "required"}><small class="hint">${person ? "Заполните для сброса пароля. " : ""}При первом входе сотрудник задаст свой пароль.</small></label>
    <input type="hidden" name="version" value="${esc(person?.version)}">
    <label class="check"><input name="active" type="checkbox" ${checked(person?.active !== false)}>Вход разрешён</label>
    <fieldset class="work-rights"><legend>Права доступа</legend><div class="list-checks">${rights}</div></fieldset>`,
    {form:"work-employee", id, submit:"Сохранить"});
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
      result = await api(id ? `/work/knowledge/${id}` : "/work/knowledge", id ? "PUT" : "POST", data);
      break;
    case "work-import":
      result = await api("/work/knowledge/import", "POST", values);
      break;
    case "work-employee":
      data.active = values.has("active");
      data.grants = values.getAll("grants");
      result = await api(id ? `/work/employees/${id}` : "/work/employees", id ? "PUT" : "POST", data);
      break;
    case "work-receive":
      result = await api("/work/inventory", "POST", data);
      break;
    case "work-move":
      result = await api(`/work/inventory/${id}/events`, "POST", data);
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
  "work-new-article": () => articleForm(null, null),
  "work-child": id => articleForm(null, id),
  "work-edit-article": () => articleForm(work.article, work.article.parent),
  "work-import": importForm,
  "work-receive": receiveForm,
  "work-asset": id => selectRecord("inventory", id),
  "work-select-employee": id => selectRecord("admin", id),
  "work-editor": () => articleForm(work.article, work.article?.parent),
  "work-edit-selected": () => employeeForm(work.person?.id),
  "work-move-selected": () => movementForm(`${work.asset.id}/${work.asset.events[0].employee ? "transfer" : "assign"}`),
  "work-move": movementForm,
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
    for (const node of input.closest(".card").querySelectorAll(".kb-tree li")) {
      node.hidden = !node.textContent.toLocaleLowerCase("ru").includes(query);
    }
    return;
  }
  input.closest(".card").querySelectorAll("[data-work-row]").forEach(row => {
    row.hidden = !row.textContent.toLocaleLowerCase("ru").includes(query);
  });
}

document.addEventListener("change", event => {
  const input = event.target;
  if (input.name !== "grants" || !input.closest('[data-form="work-employee"]')) return;
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
