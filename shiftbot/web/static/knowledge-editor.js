"use strict";

const articleEditors = new WeakMap();
const photoInputTypes = ".jpg,.jpeg,.png,.webp";
const videoInputTypes = ".mp4,.mov,.webm";

function attachmentContents(file, url, editing) {
  const caption = esc(file.name);
  const media = file.mime.startsWith("video/") ?
    `<video src="${esc(url)}" controls playsinline preload="metadata" aria-label="${caption}"></video>` :
    `<img src="${esc(url)}" loading="lazy" alt="${caption}">`;
  const remove = editing ?
    `<button class="icon-button" type="button" data-remove-media aria-label="Убрать ${caption}">${icon("close")}</button>` : "";
  return `${media}<figcaption><span>${caption}</span>${remove}</figcaption>`;
}

function articleContents(item, editing = false) {
  if (!item?.html) return item?.body ? `<p>${esc(item.body).replaceAll("\n", "<br>")}</p>` : "";
  const template = document.createElement("template");
  template.innerHTML = item.html;
  const files = new Map(item.media.map(file => [file.id, file]));
  for (const figure of template.content.querySelectorAll("figure[data-media]")) {
    const file = files.get(figure.dataset.media);
    if (!file) {
      figure.remove();
      continue;
    }
    if (editing) figure.contentEditable = "false";
    figure.innerHTML = attachmentContents(file, `/api/work/knowledge/${item.id}/media/${file.id}`, editing);
  }
  return template.innerHTML;
}

function editorToolbar() {
  const commands = [
    ["bold", "Жирный", "<strong>Ж</strong>"], ["italic", "Курсив", "<em>К</em>"],
    ["insertUnorderedList", "Маркированный список", "• Список"],
    ["insertOrderedList", "Нумерованный список", "1. Список"],
    ["undo", "Отменить", "↶"], ["redo", "Повторить", "↷"],
  ];
  return `<div class="kb-toolbar" aria-label="Форматирование статьи">
    <select aria-label="Стиль текста" data-edit-format>
      <option value="p">Абзац</option><option value="h2">Заголовок</option>
      <option value="h3">Подзаголовок</option><option value="blockquote">Цитата</option>
    </select>
    ${commands.map(([command, label, caption]) => `<button type="button" class="button" data-edit-command="${command}"
      aria-label="${label}" title="${label}">${caption}</button>`).join("")}
    <button type="button" class="button" data-pick-media="photo">Добавить фото</button>
    <button type="button" class="button" data-pick-media="video">Добавить видео</button>
    <input type="file" data-select-media="photo" accept="${photoInputTypes}" multiple hidden>
    <input type="file" data-select-media="video" accept="${videoInputTypes}" multiple hidden>
  </div>`;
}

function knowledgeEditorBody(item) {
  return `<div class="kb-edit-pane"><span id="kb-editor-label" class="kb-edit-label">Текст статьи</span>
    ${editorToolbar()}
    <div class="kb-editor kb-document" contenteditable="true" role="textbox" aria-multiline="true"
      aria-labelledby="kb-editor-label" spellcheck="true">${articleContents(item, true) || "<p><br></p>"}</div>
    <p class="hint">Фото до 5 МБ. Видео до 20 секунд и 20 МБ.</p>
    <p data-editor-error class="error-box" role="status"></p></div>`;
}

function attachArticleEditor(form) {
  const editor = form.querySelector(".kb-editor");
  const toolbar = form.querySelector(".kb-toolbar");
  const notice = form.querySelector("[data-editor-error]");
  const files = new Map();
  let range;
  articleEditors.set(form, {editor, files});

  function remember() {
    const selection = getSelection();
    if (selection.rangeCount && editor.contains(selection.anchorNode) && editor.contains(selection.focusNode)) {
      range = selection.getRangeAt(0).cloneRange();
    }
  }
  function execute(command, value) {
    editor.focus();
    const selection = getSelection();
    if (!range || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) {
      range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
    }
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand(command, false, value);
    remember();
  }
  function addFile(file, video) {
    const limit = (video ? 20 : 5) * 1024 * 1024;
    if (!file.size || file.size > limit) {
      notice.textContent = video ? "Выберите видео до 20 МБ." : "Выберите фото до 5 МБ.";
      return;
    }
    if (editor.querySelectorAll("figure[data-media]").length >= 12) {
      notice.textContent = "В статье может быть до 12 фото и видео.";
      return;
    }
    const id = Array.from(crypto.getRandomValues(new Uint8Array(8)), value => value.toString(16).padStart(2, "0")).join("");
    const mime = video ? "video/mp4" : "image/webp";
    const url = URL.createObjectURL(file);
    const html = `<figure contenteditable="false" data-media="${id}">${attachmentContents({name:file.name,mime}, url, true)}</figure><p><br></p>`;
    files.set(id, {file, url});
    range?.collapse(false);
    execute("insertHTML", html);
    if (!editor.querySelector(`[data-media="${id}"]`)) {
      editor.insertAdjacentHTML("beforeend", html);
      range = null;
    }
    if (video) {
      editor.querySelector(`[data-media="${id}"] video`)?.addEventListener("loadedmetadata", event => {
        if (event.target.isConnected && event.target.duration > 20) {
          notice.textContent = "Видео длиннее 20 секунд. Уберите его и добавьте короткий фрагмент.";
        }
      }, {once:true});
    }
  }
  editor.addEventListener("keyup", remember);
  editor.addEventListener("pointerup", remember);
  editor.addEventListener("blur", remember);
  editor.addEventListener("paste", event => {
    event.preventDefault();
    remember();
    execute("insertText", event.clipboardData.getData("text/plain"));
  });
  editor.addEventListener("drop", event => {event.preventDefault();});
  editor.addEventListener("click", event => {
    const remove = event.target.closest("[data-remove-media]");
    if (!remove) return;
    remove.closest("figure").remove();
    notice.textContent = "";
    range = null;
    editor.focus();
  });
  toolbar.addEventListener("pointerdown", event => {
    remember();
    if (event.target.closest("button")) event.preventDefault();
  });
  toolbar.addEventListener("click", event => {
    const command = event.target.closest("[data-edit-command]");
    const pick = event.target.closest("[data-pick-media]");
    if (command) execute(command.dataset.editCommand);
    if (pick) toolbar.querySelector(`[data-select-media="${pick.dataset.pickMedia}"]`).click();
  });
  toolbar.addEventListener("change", event => {
    if (event.target.hasAttribute("data-edit-format")) {
      execute("formatBlock", event.target.value);
    } else if (event.target.hasAttribute("data-select-media")) {
      notice.textContent = "";
      for (const file of event.target.files) addFile(file, event.target.dataset.selectMedia === "video");
      event.target.value = "";
    }
  });
  drawer.addEventListener("close", () => {
    for (const {url} of files.values()) URL.revokeObjectURL(url);
  }, {once:true});
}

function articlePayload(form, data) {
  const context = articleEditors.get(form);
  if (!context) return data;
  const documentCopy = context.editor.cloneNode(true);
  const attachments = new FormData();
  let hasUploads = false;
  for (const figure of documentCopy.querySelectorAll("figure[data-media]")) {
    const id = figure.dataset.media;
    figure.replaceChildren();
    figure.removeAttribute("contenteditable");
    if (context.files.has(id)) {
      attachments.set(`media:${id}`, context.files.get(id).file);
      hasUploads = true;
    }
  }
  data.html = documentCopy.innerHTML;
  if (!hasUploads) return data;
  attachments.set("record", JSON.stringify(data));
  return attachments;
}
