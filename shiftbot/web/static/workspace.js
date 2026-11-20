"use strict";

const workSources = {knowledge: "knowledge", inventory: "inventory", admin: "employees"};
const workClock = new Intl.DateTimeFormat("ru-RU", {timeZone:"Europe/Moscow", dateStyle:"short", timeStyle:"short"});
const work = {page: "", data: null, error: "", currentArticle: "", article: null, linkedAsset: "", request: 0};

function isWorkPage(page) {
  return Object.hasOwn(workSources, page);
}

async function loadWork() {
  const page = ui.page;
  if (!isWorkPage(page)) return;
  const request = ++work.request;
  try {
    const data = await api(`/work/${workSources[page]}`);
    let article = null;
    if (page === "knowledge" && data.items.length) {
      const target = data.items.find(item => item.id === work.currentArticle) || data.items[0];
      article = await api(`/work/knowledge/${target.id}`);
    }
    if (ui.page !== page || work.request !== request) return;
    Object.assign(work, {page, data, article, error: ""});
    if (article) work.currentArticle = article.id;
  } catch (error) {
    if (ui.page !== page || work.request !== request) return;
    Object.assign(work, {page, data: null, error: error.message});
  }
  render();
  if (page === "inventory" && work.data && work.linkedAsset) {
    const id = work.linkedAsset;
    work.linkedAsset = "";
    try {
      await openAsset(id);
    } catch (error) {
      toast(error.message, true);
    }
  }
}

function workPage() {
  if (work.page !== ui.page) return `<p class="muted" role="status">Загрузка…</p>`;
  if (work.error) return empty("alert", "Раздел не загрузился", work.error, button("work-reload", "Повторить"));
  return {knowledge: knowledgePage, inventory: inventoryPage, admin: employeePage}[ui.page]();
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
    const active = item.id === work.currentArticle ? 'aria-current="true"' : "";
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
    <div class="card-body work-tree-search"><label class="search">${icon("search")}<input type="search" data-article-search aria-label="Найти пункт" placeholder="Найти пункт"></label></div>
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
  if (!work.data.items.length) return top + `<section class="card">${empty("device", "В реестре пока нет техники")}</section>`;
  return top + `<section class="card"><div class="card-head"><h2>Реестр техники</h2>${workSearch("Номер, техника или сотрудник")}</div>
    <div class="table-wrap"><table><thead><tr><th>Инв. номер</th><th>Техника</th><th>Состояние</th><th>Закреплена за</th><th>Дата приёмки</th></tr></thead>
    <tbody>${work.data.items.map(asset => `<tr data-work-row data-action="work-asset" data-id="${esc(asset.id)}">
      <td><button type="button" class="point-button" data-action="work-asset" data-id="${esc(asset.id)}">${esc(asset.tag)}</button></td>
      <td>${esc(asset.name)}<small class="work-serial">${esc(asset.serial)}</small></td>
      <td>${badge(work.data.states[asset.state], asset.state === "in_use" ? "green" : "blue")}</td>
      <td>${esc(asset.employee_name || "—")}</td><td class="tabular">${esc(reportDate(asset.received_on))}</td></tr>`).join("")}</tbody></table></div></section>`;
}

function workSearch(label) {
  return `<label class="search">${icon("search")}<input type="search" data-work-search aria-label="${esc(label)}" placeholder="${esc(label)}"></label>`;
}

function receiveForm() {
  drawerView("Приёмка техники", "", `
    <label>Инвентарный номер<input name="tag" required maxlength="100"></label>
    <label>Наименование<input name="name" required maxlength="160"></label>
    <label>Серийный номер<input name="serial" maxlength="120"></label>
    <label>Дата приёмки<input type="date" name="day" required value="${work.data.today}" max="${work.data.today}"></label>
    <label>Документ или комментарий<input name="note" maxlength="500"></label>`, {form:"work-receive", submit:"Принять на склад"});
}

async function openAsset(id) {
  const item = await api(`/work/inventory/${id}`);
  if (ui.page !== "inventory") return;
  const last = item.events[0];
  const actions = work.data.actions;
  const list = item.events.map(event => `<li><strong>${esc(actions[event.action])}</strong>
    <span>${esc(reportDate(event.day))}${event.employee_name ? " · " + esc(event.employee_name) : ""}</span>
    <small>Оформил ${esc(event.actor_name)} · ${esc(workClock.format(new Date(event.recorded_at)))} МСК</small>
    ${event.note ? `<p>${esc(event.note)}</p>` : ""}</li>`).join("");
  const moves = last.state === "stock" ? ["assign","commission"] : last.employee ? ["transfer","return"] : ["assign","return"];
  const buttons = grants.has("inventory.write") ? moves.map(action => button("work-move", actions[action], {id:`${id}/${action}`})).join("") : "";
  drawerView(item.name, item.tag, `<div class="row">${badge(work.data.states[last.state], last.state === "in_use" ? "green" : "blue")}
    ${esc(last.employee_name)}</div>${item.serial ? `<p>Серийный номер · ${esc(item.serial)}</p>` : ""}
    <div class="row">${buttons}</div><section><h3>Журнал движения</h3><ol class="movement-list">${list}</ol></section>`);
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
  const top = pageHead(labels.admin, "", button("work-employee", "Добавить сотрудника", {kind:"primary",icon:"plus"}));
  if (!work.data.items.length) return top + `<section class="card">${empty("users", "Сотрудники пока не добавлены")}</section>`;
  return top + `<section class="card"><div class="card-head"><h2>Сотрудники</h2>${workSearch("Имя или логин")}</div>
    <div class="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Логин</th><th>Доступ</th><th>Вход</th></tr></thead>
    <tbody>${work.data.items.map(person => `<tr data-work-row data-action="work-employee" data-id="${esc(person.id)}">
      <td><button type="button" class="point-button" data-action="work-employee" data-id="${esc(person.id)}">${esc(person.name)}</button></td>
      <td>${esc(person.login)}</td><td>${badge(person.active ? "Активен" : "Заблокирован", person.active ? "green" : "red")}</td>
      <td>${person.must_change ? "Требуется смена пароля" : "Личный пароль"}</td></tr>`).join("")}</tbody></table></div></section>`;
}

async function employeeForm(id) {
  const person = work.data.items.find(item => item.id === id);
  const assigned = person ? (await api(`/work/inventory?employee=${encodeURIComponent(id)}`)).items : [];
  if (ui.page !== "admin") return;
  const assets = person ? `<section class="stack"><h3>Закреплённая техника</h3>
    ${assigned.length ? assigned.map(item => `<a class="button work-file" href="#inventory/${esc(item.id)}">${icon("device")}${esc(item.tag)} · ${esc(item.name)}</a>`).join("") : '<p class="muted">Техника не закреплена.</p>'}</section>` : "";
  const rights = Object.entries(work.data.permissions).map(([key, label]) =>
    `<label class="check"><input type="checkbox" name="grants" value="${esc(key)}" ${checked(person?.grants.includes(key))}>${esc(label)}</label>`).join("");
  drawerView(person ? "Сотрудник" : "Новый сотрудник", "", `
    <label>Имя сотрудника<input name="name" maxlength="160" value="${esc(person?.name)}" required></label>
    <label>Логин<input name="login" maxlength="120" value="${esc(person?.login)}" autocomplete="off" required></label>
    <label>${person ? "Новый временный пароль" : "Временный пароль"}<input type="password" name="password" minlength="12" maxlength="256"
      autocomplete="new-password" ${person ? "" : "required"}><small class="hint">${person ? "Заполните для сброса пароля. " : ""}При первом входе сотрудник задаст свой пароль.</small></label>
    <input type="hidden" name="version" value="${esc(person?.version)}">
    <label class="check"><input name="active" type="checkbox" ${checked(person?.active !== false)}>Вход разрешён</label>
    <fieldset class="work-rights"><legend>Права доступа</legend><div class="list-checks">${rights}</div></fieldset>${assets}`,
    {form:"work-employee", id, submit:"Сохранить"});
}

async function submitWork(form) {
  const values = new FormData(form);
  const data = Object.fromEntries(values);
  if (data.version) data.version = Number(data.version);
  const id = form.dataset.id;
  let result;
  switch (form.dataset.form) {
    case "work-article":
      result = await api(id ? `/work/knowledge/${id}` : "/work/knowledge", id ? "PUT" : "POST", data);
      work.currentArticle = result.id;
      break;
    case "work-import":
      result = await api("/work/knowledge/import", "POST", values);
      work.currentArticle = result.id;
      break;
    case "work-employee":
      data.active = values.has("active");
      data.grants = values.getAll("grants");
      await api(id ? `/work/employees/${id}` : "/work/employees", id ? "PUT" : "POST", data);
      break;
    case "work-receive":
      result = await api("/work/inventory", "POST", data);
      break;
    case "work-move":
      result = await api(`/work/inventory/${id}/events`, "POST", data);
      break;
  }
  if (result && ui.page === "knowledge") history.replaceState(null, "", `#knowledge/${result.id}`);
  drawer.close();
  await loadWork();
  if (result && ui.page === "inventory") await openAsset(result.id);
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
  "work-asset": openAsset,
  "work-move": movementForm,
  "work-employee": employeeForm,
};

document.addEventListener("input", event => {
  if (!event.target.matches("[data-work-search], [data-article-search]")) return;
  const query = event.target.value.trim().toLocaleLowerCase("ru");
  if (event.target.hasAttribute("data-article-search")) {
    for (const node of event.target.closest(".card").querySelectorAll(".kb-tree li")) {
      node.hidden = !node.textContent.toLocaleLowerCase("ru").includes(query);
    }
    return;
  }
  event.target.closest(".card").querySelectorAll("[data-work-row]").forEach(row => {
    row.hidden = !row.textContent.toLocaleLowerCase("ru").includes(query);
  });
});

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
