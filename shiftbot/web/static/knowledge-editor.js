"use strict";

const articleEditors = new WeakMap();
const photoInputTypes = ".jpg,.jpeg,.png,.webp";
const videoInputTypes = ".mp4,.mov,.webm";
const downloadMime = "application/octet-stream";
const photoSizes = ["25", "50", "75", "100"];
const alignments = [["left", "Слева"], ["center", "По центру"], ["right", "Справа"]];
const textBlocks = "p,div,h2,h3,h4,blockquote,pre,li";
const mediaDragType = "application/x-uppetit-attachment";

function attachmentContents(file, url, figure, editing) {
  const caption = esc(file.name);
  const source = esc(url);
  const image = file.mime.startsWith("image/");
  const photoTools = image ? `
    <button class="button" type="button" data-replace-media>Заменить фото</button>
    <label>Размер фото<select data-media-width aria-label="Размер фото">
      <option value="">Исходный</option>
      ${photoSizes.map(size => `<option value="${size}" ${selected(figure.dataset.width === size)}>${size}%</option>`).join("")}
    </select></label>
    <label>Положение фото<select data-media-align aria-label="Положение фото">
      ${alignments.map(([value, label]) => `<option value="${value}" ${selected((figure.dataset.align || "left") === value)}>${label}</option>`).join("")}
    </select></label>` : "";
  const tools = editing ? `<div class="kb-media-tools" role="group" aria-label="Вложение · ${caption}">
    <button class="button kb-media-drag" type="button" draggable="true" data-drag-media title="Перетащить вложение">↕ Перетащить</button>
    <button class="button" type="button" data-shift-media="up" aria-label="Переместить выше" title="Переместить выше">↑</button>
    <button class="button" type="button" data-shift-media="down" aria-label="Переместить ниже" title="Переместить ниже">↓</button>
    ${photoTools}
    <button class="icon-button" type="button" data-remove-media aria-label="Убрать ${caption}">${icon("close")}</button>
  </div>` : "";
  if (file.mime === downloadMime) {
    return `${tools}<a class="button work-file kb-download" href="${source}" download="${caption}">${icon("list")}${caption}</a>`;
  }
  const media = image ?
    `<img src="${source}" loading="lazy" alt="${caption}" draggable="false">` :
    `<video src="${source}" controls playsinline preload="metadata" aria-label="${caption}"></video>`;
  return `${tools}${media}<figcaption>${caption}</figcaption>`;
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
    figure.innerHTML = attachmentContents(file, `/api/work/knowledge/${item.id}/media/${file.id}`, figure, editing);
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
  const alignLabels = {left:"по левому краю", center:"по центру", right:"по правому краю"};
  const commandButtons = items => items.map(([command, label, caption]) =>
    `<button type="button" class="button" data-edit-command="${command}" aria-label="${label}" title="${label}">${caption}</button>`).join("");
  return `<div class="kb-toolbar" aria-label="Форматирование статьи">
    <select aria-label="Стиль текста" data-edit-format>
      <option value="p">Абзац</option><option value="h2">Заголовок</option>
      <option value="h3">Подзаголовок</option><option value="blockquote">Цитата</option>
    </select>
    ${commandButtons(commands)}
    <div class="kb-align-tools" role="group" aria-label="Выравнивание текста">
      ${alignments.map(([value, caption]) => `<button type="button" class="button" data-edit-align="${value}"
        aria-label="Выровнять ${alignLabels[value]}" title="Выровнять ${alignLabels[value]}">${caption}</button>`).join("")}
    </div>
    <button type="button" class="button" data-pick-media>Добавить файлы</button>
    <input type="file" data-select-media multiple hidden>
  </div>`;
}

function knowledgeEditorBody(item) {
  return `<div class="kb-edit-pane"><span id="kb-editor-label" class="kb-edit-label">Текст статьи</span>
    ${editorToolbar()}
    <div class="kb-editor kb-document" contenteditable="true" role="textbox" aria-multiline="true"
      aria-labelledby="kb-editor-label" spellcheck="true">${articleContents(item, true) || "<p><br></p>"}</div>
    <p class="hint">Перетащите файлы в статью. Фото до 5 МБ, другие файлы до 20 МБ, видео до 20 секунд.</p>
    <p data-editor-error class="error-box" role="status"></p></div>`;
}

function fileMime(file) {
  const extension = file.name.toLowerCase().match(/.+(\.[^.]+)$/)?.[1];
  if (photoInputTypes.split(",").includes(extension)) return "image/webp";
  if (videoInputTypes.split(",").includes(extension)) return "video/mp4";
  return downloadMime;
}

function attachArticleEditor(form) {
  const editor = form.querySelector(".kb-editor");
  const toolbar = form.querySelector(".kb-toolbar");
  const picker = toolbar.querySelector("[data-select-media]");
  const notice = form.querySelector("[data-editor-error]");
  const files = new Map();
  let range, replacement, draggedFigure;
  articleEditors.set(form, {editor, files});

  function remember() {
    const selection = getSelection();
    if (selection.rangeCount && editor.contains(selection.anchorNode) && editor.contains(selection.focusNode)) {
      range = selection.getRangeAt(0).cloneRange();
    }
  }

  function restoreSelection() {
    editor.focus();
    const selection = getSelection();
    if (!range || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) {
      range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
    }
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function execute(command, value) {
    restoreSelection();
    document.execCommand(command, false, value);
    remember();
  }

  function alignText(alignment) {
    restoreSelection();
    const blocks = new Set();
    const addBlock = node => {
      const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
      const block = element.closest(textBlocks);
      if (block !== editor && editor.contains(block) && !element.closest("figure")) blocks.add(block);
    };
    addBlock(range.startContainer);
    addBlock(range.endContainer);
    if (!range.collapsed) {
      const walker = document.createTreeWalker(range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (range.intersectsNode(node)) addBlock(node);
      }
    }
    if (!blocks.size) {
      execute("formatBlock", "p");
      addBlock(range.startContainer);
    }
    for (const block of blocks) block.dataset.align = alignment;
    remember();
  }

  function addFile(file, replace) {
    const mime = fileMime(file);
    const image = mime.startsWith("image/");
    if (replace && !image) {
      notice.textContent = "Выберите фото JPG, PNG или WebP.";
      return;
    }
    const limit = image ? 5 : 20;
    if (!file.size || file.size > limit * 1024 * 1024) {
      notice.textContent = `Выберите непустой файл до ${limit} МБ.`;
      return;
    }
    if (!replace && editor.querySelectorAll("figure[data-media]").length >= 12) {
      notice.textContent = "В статье может быть до 12 вложений.";
      return;
    }
    const id = Array.from(crypto.getRandomValues(new Uint8Array(8)), value => value.toString(16).padStart(2, "0")).join("");
    const url = URL.createObjectURL(file);
    const figure = replace || document.createElement("figure");
    figure.contentEditable = "false";
    figure.dataset.media = id;
    figure.innerHTML = attachmentContents({name:file.name, mime}, url, figure, true);
    files.set(id, {file, url});
    if (!replace) {
      const html = figure.outerHTML + "<p><br></p>";
      range?.collapse(false);
      execute("insertHTML", html);
      if (!editor.querySelector(`[data-media="${id}"]`)) {
        editor.insertAdjacentHTML("beforeend", html);
        range = null;
      }
    }
    editor.querySelector(`[data-media="${id}"] video`)?.addEventListener("loadedmetadata", event => {
      if (event.target.isConnected && event.target.duration > 20) {
        notice.textContent = "Видео длиннее 20 секунд. Уберите его и добавьте короткий фрагмент.";
      }
    }, {once:true});
  }

  function chooseFiles(figure = null) {
    replacement = figure;
    picker.multiple = !figure;
    picker.accept = figure ? photoInputTypes : "";
    picker.click();
  }

  function editorBlock(node) {
    if (!node || node === editor || !editor.contains(node)) return null;
    while (node.parentNode !== editor) node = node.parentNode;
    return node;
  }

  function dropPosition(event) {
    const block = editorBlock(event.target) || [...editor.children].find(child => event.clientY <= child.getBoundingClientRect().bottom);
    const box = block?.getBoundingClientRect?.();
    return {block, before: box && event.clientY < box.top + box.height / 2};
  }

  function dropFiles(event) {
    const caret = document.caretPositionFromPoint?.(event.clientX, event.clientY);
    range = document.caretRangeFromPoint?.(event.clientX, event.clientY);
    if (caret) {
      range = document.createRange();
      range.setStart(caret.offsetNode, caret.offset);
    }
    const {block, before} = dropPosition(event);
    const origin = range?.startContainer;
    if (!editor.contains(origin) || block?.nodeName === "FIGURE" || origin?.parentElement?.closest("figure")) {
      range = document.createRange();
      if (!block) range.selectNodeContents(editor);
      else if (before) range.setStartBefore(block);
      else range.setStartAfter(block);
    }
    range.collapse(Boolean(block));
    notice.textContent = "";
    for (const file of event.dataTransfer.files) addFile(file);
  }

  editor.addEventListener("keyup", remember);
  editor.addEventListener("pointerup", remember);
  editor.addEventListener("blur", remember);
  editor.addEventListener("paste", event => {
    event.preventDefault();
    remember();
    execute("insertText", event.clipboardData.getData("text/plain"));
  });
  editor.addEventListener("dragstart", event => {
    const handle = event.target.closest("[data-drag-media]");
    if (!handle) return;
    draggedFigure = handle.closest("figure");
    event.dataTransfer.setData(mediaDragType, draggedFigure.dataset.media);
    event.dataTransfer.effectAllowed = "move";
  });
  editor.addEventListener("dragend", () => {
    draggedFigure = null;
    editor.classList.remove("is-drop-target");
  });
  editor.addEventListener("dragover", event => {
    if (!draggedFigure && !event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = draggedFigure ? "move" : "copy";
    editor.classList.add("is-drop-target");
  });
  editor.addEventListener("dragleave", event => {
    if (!editor.contains(event.relatedTarget)) editor.classList.remove("is-drop-target");
  });
  editor.addEventListener("drop", event => {
    event.preventDefault();
    editor.classList.remove("is-drop-target");
    if (event.dataTransfer.files.length) {
      dropFiles(event);
    } else if (draggedFigure) {
      const {block, before} = dropPosition(event);
      if (!block) editor.append(draggedFigure);
      else if (!block.contains(draggedFigure)) {
        if (before) block.before(draggedFigure);
        else block.after(draggedFigure);
      }
      draggedFigure = null;
      range = null;
    }
  });
  editor.addEventListener("click", event => {
    const figure = event.target.closest("figure[data-media]");
    if (!figure) return;
    const shift = event.target.closest("[data-shift-media]");
    if (event.target.closest("[data-replace-media]")) {
      chooseFiles(figure);
    } else if (event.target.closest("[data-remove-media]")) {
      figure.remove();
      notice.textContent = "";
      range = null;
      editor.focus();
    } else if (shift) {
      const block = editorBlock(figure);
      if (shift.dataset.shiftMedia === "up") block.previousElementSibling?.before(figure);
      else block.nextElementSibling?.after(figure);
      range = null;
      shift.focus();
    }
  });
  editor.addEventListener("change", event => {
    const figure = event.target.closest("figure[data-media]");
    if (event.target.hasAttribute("data-media-width")) {
      if (event.target.value) figure.dataset.width = event.target.value;
      else delete figure.dataset.width;
    }
    if (event.target.hasAttribute("data-media-align")) figure.dataset.align = event.target.value;
  });
  toolbar.addEventListener("pointerdown", event => {
    remember();
    if (event.target.closest("button")) event.preventDefault();
  });
  toolbar.addEventListener("click", event => {
    const command = event.target.closest("[data-edit-command]");
    const alignment = event.target.closest("[data-edit-align]");
    if (command) execute(command.dataset.editCommand);
    if (alignment) alignText(alignment.dataset.editAlign);
    if (event.target.closest("[data-pick-media]")) chooseFiles();
  });
  toolbar.addEventListener("change", event => {
    if (event.target.hasAttribute("data-edit-format")) execute("formatBlock", event.target.value);
  });
  picker.addEventListener("change", () => {
    notice.textContent = "";
    for (const file of picker.files) addFile(file, replacement?.isConnected ? replacement : null);
    picker.value = "";
    replacement = null;
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
