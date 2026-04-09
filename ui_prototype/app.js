const BASE_SLOT_HEIGHT = 1.2;

const fallbackProblems = [
  {
    id: "math-01",
    title: "01. Function graph warm-up",
    subject: "math",
    imagePath: "../out_images_sample4/record_0000_img_0.jpg",
    actualHeightPages: 0.92,
    overflowAllowed: false,
    readingHeavy: false,
  },
  {
    id: "korean-02",
    title: "02. Long reading passage",
    subject: "korean",
    imagePath: "../out_images_sample4/record_0001_img_0.jpg",
    actualHeightPages: 1.46,
    overflowAllowed: true,
    readingHeavy: true,
  },
  {
    id: "science-03",
    title: "03. Chemistry concept check",
    subject: "science",
    imagePath: "../out_images_sample4/record_0002_img_0.jpg",
    actualHeightPages: 1.04,
    overflowAllowed: false,
    readingHeavy: false,
  },
  {
    id: "english-04",
    title: "04. Reading set with choices",
    subject: "english",
    imagePath: "../out_images_sample4/record_0003_img_0.jpg",
    actualHeightPages: 1.34,
    overflowAllowed: true,
    readingHeavy: true,
  },
];

const templatePresets = {
  "academy-default": {
    name: "Academy default",
    baseSlotHeight: 1.2,
    boardPageCount: 50,
    fixedLeftRatio: 0.5,
  },
  "korean-reading": {
    name: "Korean reading",
    baseSlotHeight: 1.2,
    boardPageCount: 50,
    fixedLeftRatio: 0.54,
  },
  "exam-review": {
    name: "Exam review",
    baseSlotHeight: 1.2,
    boardPageCount: 50,
    fixedLeftRatio: 0.48,
  },
};

function toNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function normalizePath(value) {
  if (!value) {
    return "";
  }
  if (value.startsWith("file://") || value.startsWith("http://") || value.startsWith("https://") || value.startsWith("/api/")) {
    return value;
  }
  if (/^[A-Za-z]:[\\/]/.test(value)) {
    return `file:///${value.replace(/\\/g, "/")}`;
  }
  return value.replace(/\\/g, "/");
}

function normalizeTemplate(template) {
  if (!template) {
    return null;
  }
  return {
    name: template.name || "Generated session",
    baseSlotHeight: toNumber(template.base_slot_height_pages ?? template.baseSlotHeight, BASE_SLOT_HEIGHT),
    boardPageCount: toNumber(template.board_page_count ?? template.boardPageCount, 50),
    fixedLeftRatio: toNumber(template.fixed_left_zone_ratio ?? template.fixedLeftRatio, 0.5),
  };
}

function normalizeProblem(problem, index) {
  return {
    id: problem.id || problem.problem_id || `problem-${String(index + 1).padStart(2, "0")}`,
    title: problem.title || `Problem ${index + 1}`,
    subject: problem.subject || "unknown",
    imagePath: normalizePath(problem.imagePath || problem.image_path || problem.sourceImagePath || ""),
    sourceImagePath: normalizePath(problem.sourceImagePath || problem.source_image_path || problem.imagePath || ""),
    boardRenderPath: normalizePath(problem.boardRenderPath || problem.board_render_path || ""),
    actualHeightPages: toNumber(problem.actualHeightPages ?? problem.actual_content_height_pages, 0.9),
    overflowAllowed: Boolean(problem.overflowAllowed ?? problem.overflow_allowed),
    readingHeavy: Boolean(problem.readingHeavy ?? problem.reading_heavy),
    sourcePageId: problem.sourcePageId || problem.source_page_id || "",
    startYPages: toNumber(problem.startYPages ?? problem.start_y_pages, 0),
    snappedNextStartYPages: toNumber(problem.snappedNextStartYPages ?? problem.snapped_next_start_y_pages, 0),
    overflowAmountPages: toNumber(problem.overflowAmountPages ?? problem.overflow_amount_pages, 0),
    overflowViolation: Boolean(problem.overflowViolation ?? problem.overflow_violation),
    slotSpanCount: toNumber(problem.slotSpanCount ?? problem.slot_span_count, 1),
  };
}

function normalizeSession(rawSession, fallbackName = "Loaded session") {
  const rawProblems = Array.isArray(rawSession?.problems) ? rawSession.problems : [];
  return {
    sessionName: rawSession?.session_name || rawSession?.sessionName || fallbackName,
    dataSource: rawSession?.data_source || rawSession?.dataSource || "manual",
    generatedAt: rawSession?.generated_at || rawSession?.generatedAt || "",
    outputDir: rawSession?.output_dir || rawSession?.outputDir || "",
    pagesJsonPath: rawSession?.pages_json_path || rawSession?.pagesJsonPath || "",
    placementsJsonPath: rawSession?.placements_json_path || rawSession?.placementsJsonPath || "",
    edbPath: rawSession?.edb_path || rawSession?.edbPath || "",
    edbFileUri: normalizePath(rawSession?.edb_file_uri || rawSession?.edbFileUri || rawSession?.edb_path || ""),
    renderedPageFileUris: (rawSession?.rendered_page_file_uris || rawSession?.renderedPageFileUris || rawSession?.rendered_page_paths || rawSession?.renderedPagePaths || []).map(normalizePath),
    template: normalizeTemplate(rawSession?.template),
    problems: rawProblems.map(normalizeProblem),
  };
}

function cloneProblems(problems) {
  return problems.map((problem) => ({ ...problem }));
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const commaIndex = result.indexOf(",");
      resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

const sampleSession = normalizeSession(
  {
    session_name: "Prototype sample",
    data_source: "sample",
    problems: window.PROTOTYPE_DATA?.problems || fallbackProblems,
  },
  "Prototype sample",
);

const generatedSession = window.EDB_UI_SESSION
  ? normalizeSession(window.EDB_UI_SESSION, "Generated session")
  : null;

const state = {
  session: generatedSession || sampleSession,
  problems: cloneProblems((generatedSession || sampleSession).problems),
  selectedId: (generatedSession || sampleSession).problems[0]?.id || null,
  previewMode: "board",
  templateKey: "academy-default",
  dragId: null,
  apiAvailable: false,
  runBusy: false,
  runSourceFile: null,
};

function syncTemplateSelect() {
  const select = document.getElementById("templateSelect");
  const sessionTemplate = state.session.template;
  const generatedOptionValue = "generated-session";
  const existingGeneratedOption = select.querySelector(`option[value="${generatedOptionValue}"]`);

  if (sessionTemplate) {
    templatePresets[generatedOptionValue] = {
      name: sessionTemplate.name,
      baseSlotHeight: sessionTemplate.baseSlotHeight,
      boardPageCount: sessionTemplate.boardPageCount,
      fixedLeftRatio: sessionTemplate.fixedLeftRatio,
    };
    if (!existingGeneratedOption) {
      const option = document.createElement("option");
      option.value = generatedOptionValue;
      option.textContent = `${sessionTemplate.name} (Session)`;
      select.prepend(option);
    } else {
      existingGeneratedOption.textContent = `${sessionTemplate.name} (Session)`;
    }
    state.templateKey = generatedOptionValue;
  } else if (existingGeneratedOption) {
    existingGeneratedOption.remove();
    if (state.templateKey === generatedOptionValue) {
      state.templateKey = "academy-default";
    }
  }

  select.value = state.templateKey;
}

function applySession(session) {
  state.session = session;
  state.problems = cloneProblems(session.problems);
  state.selectedId = session.problems[0]?.id || null;
  syncTemplateSelect();
  render();
}

function getTemplate() {
  return templatePresets[state.templateKey] || templatePresets["academy-default"];
}

function snapUp(value, baseSlotHeight = BASE_SLOT_HEIGHT) {
  if (value <= 0) {
    return 0;
  }
  return Math.ceil((value - 1e-9) / baseSlotHeight) * baseSlotHeight;
}

function computePlacements() {
  const template = getTemplate();
  let cursor = 0;

  return state.problems.map((problem) => {
    const start = snapUp(cursor, template.baseSlotHeight);
    const actualBottom = Number((start + problem.actualHeightPages).toFixed(2));
    const nextStart = Number(snapUp(actualBottom, template.baseSlotHeight).toFixed(2));
    const overflowAmount = Math.max(0, problem.actualHeightPages - template.baseSlotHeight);

    cursor = nextStart;

    return {
      ...problem,
      startYPages: start,
      actualBottomYPages: actualBottom,
      snappedNextStartYPages: nextStart,
      overflowAmountPages: Number(overflowAmount.toFixed(2)),
      overflowViolation: overflowAmount > 0 && !problem.overflowAllowed,
      slotSpanCount: Math.max(1, Math.round((nextStart - start) / template.baseSlotHeight)),
    };
  });
}

function moveProblem(problemId, delta) {
  const index = state.problems.findIndex((item) => item.id === problemId);
  const targetIndex = index + delta;
  if (index < 0 || targetIndex < 0 || targetIndex >= state.problems.length) {
    return;
  }

  const next = [...state.problems];
  const [moved] = next.splice(index, 1);
  next.splice(targetIndex, 0, moved);
  state.problems = next;
  render();
}

function duplicateProblem(problemId) {
  const index = state.problems.findIndex((item) => item.id === problemId);
  if (index < 0) {
    return;
  }

  const source = state.problems[index];
  const copy = {
    ...source,
    id: `${source.id}-copy-${Date.now()}`,
    title: `${source.title} copy`,
  };
  const next = [...state.problems];
  next.splice(index + 1, 0, copy);
  state.problems = next;
  state.selectedId = copy.id;
  render();
}

function deleteProblem(problemId) {
  if (state.problems.length === 1) {
    return;
  }

  const index = state.problems.findIndex((item) => item.id === problemId);
  if (index < 0) {
    return;
  }

  state.problems = state.problems.filter((item) => item.id !== problemId);
  if (state.selectedId === problemId) {
    state.selectedId = state.problems[Math.max(0, index - 1)]?.id || state.problems[0]?.id || null;
  }
  render();
}

function addLongPassage() {
  const nextId = `korean-${String(state.problems.length + 1).padStart(2, "0")}`;
  state.problems.push({
    id: nextId,
    title: "New long Korean passage",
    subject: "korean",
    imagePath: state.problems[0]?.imagePath || "",
    sourceImagePath: state.problems[0]?.sourceImagePath || "",
    boardRenderPath: state.problems[0]?.boardRenderPath || "",
    actualHeightPages: 1.58,
    overflowAllowed: true,
    readingHeavy: true,
    sourcePageId: "",
    startYPages: 0,
    snappedNextStartYPages: 0,
    overflowAmountPages: 0,
    overflowViolation: false,
    slotSpanCount: 1,
  });
  state.selectedId = nextId;
  render();
}

function updateProblem(problemId, patch) {
  state.problems = state.problems.map((item) => (
    item.id === problemId ? { ...item, ...patch } : item
  ));
  render();
}

function reorderProblems(dragId, dropId) {
  if (!dragId || !dropId || dragId === dropId) {
    return;
  }

  const next = [...state.problems];
  const fromIndex = next.findIndex((item) => item.id === dragId);
  const toIndex = next.findIndex((item) => item.id === dropId);
  if (fromIndex < 0 || toIndex < 0) {
    return;
  }

  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved);
  state.problems = next;
  render();
}

function setRunStatus(message, tone = "neutral") {
  const node = document.getElementById("runStatusText");
  node.textContent = message;
  node.dataset.tone = tone;
}

function updateRuntimeControls() {
  const apiStatus = document.getElementById("apiStatus");
  const selectedSourceName = document.getElementById("selectedSourceName");
  const runExportButton = document.getElementById("runExportButton");

  apiStatus.textContent = state.apiAvailable ? "local app connected" : "offline preview";
  apiStatus.classList.toggle("is-connected", state.apiAvailable);
  apiStatus.classList.toggle("is-offline", !state.apiAvailable);

  selectedSourceName.textContent = state.runSourceFile ? state.runSourceFile.name : "no source selected";
  runExportButton.disabled = !state.apiAvailable || state.runBusy;
}

async function probeApi() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error(`health ${response.status}`);
    }
    state.apiAvailable = true;
    setRunStatus("Local export API connected. Choose a source and run export.", "success");
  } catch (error) {
    state.apiAvailable = false;
    setRunStatus("Static preview mode. Start app_server.py to enable in-browser export.", "warning");
  }
  updateRuntimeControls();
}

async function fetchLatestSessionFromApi() {
  const response = await fetch("/api/session/latest");
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `Failed to load latest session (${response.status})`);
  }
  return normalizeSession(payload.session, "Latest session");
}

async function runExportFromApi() {
  if (!state.apiAvailable) {
    window.alert("Local export API is not connected. Start app_server.py first.");
    return;
  }
  if (!state.runSourceFile) {
    window.alert("Choose a source image or PDF first.");
    return;
  }

  const runExportButton = document.getElementById("runExportButton");
  try {
    state.runBusy = true;
    runExportButton.disabled = true;
    setRunStatus("Uploading source and running MVP export...", "loading");

    const payload = {
      fileName: state.runSourceFile.name,
      fileDataBase64: await fileToBase64(state.runSourceFile),
      outputDir: document.getElementById("outputDirInput").value.trim(),
      subject: document.getElementById("runSubjectSelect").value,
      ocr: document.getElementById("runOcrSelect").value,
      exportEdb: document.getElementById("runExportEdb").checked,
    };

    const response = await fetch("/api/export", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || `Export failed (${response.status})`);
    }

    applySession(normalizeSession(result.session, "Export session"));
    setRunStatus(`Export complete: ${result.outputDir}`, "success");
  } catch (error) {
    setRunStatus(`Export failed: ${error.message}`, "error");
    window.alert(`Export failed: ${error.message}`);
  } finally {
    state.runBusy = false;
    updateRuntimeControls();
  }
}

function renderFilmstrip(placements) {
  const root = document.getElementById("filmstripList");
  root.innerHTML = "";

  placements.forEach((item, index) => {
    const entry = document.createElement("button");
    entry.type = "button";
    entry.className = `film-item${item.id === state.selectedId ? " is-selected" : ""}`;
    entry.draggable = true;
    entry.dataset.problemId = item.id;
    entry.innerHTML = `
      <img class="film-thumb" src="${item.imagePath}" alt="${item.title}">
      <div class="film-copy">
        <div class="film-index">${String(index + 1).padStart(2, "0")} | start ${item.startYPages.toFixed(1)}p</div>
        <div class="film-title">${item.title}</div>
        <div class="film-meta">
          <span>${item.subject}</span>
          <span>${item.actualHeightPages.toFixed(2)}p tall</span>
          <span>${item.snappedNextStartYPages.toFixed(1)}p next</span>
          <span>${item.overflowAllowed ? "overflow ok" : "fit first"}</span>
        </div>
      </div>
    `;

    entry.addEventListener("click", () => {
      state.selectedId = item.id;
      render();
    });
    entry.addEventListener("dragstart", () => {
      state.dragId = item.id;
    });
    entry.addEventListener("dragend", () => {
      state.dragId = null;
      render();
    });
    entry.addEventListener("dragover", (event) => {
      event.preventDefault();
      entry.classList.add("is-drag-target");
    });
    entry.addEventListener("dragleave", () => {
      entry.classList.remove("is-drag-target");
    });
    entry.addEventListener("drop", (event) => {
      event.preventDefault();
      entry.classList.remove("is-drag-target");
      reorderProblems(state.dragId, item.id);
      state.dragId = null;
    });

    root.appendChild(entry);
  });
}

function renderImagePreview(title, subtitle, imagePath) {
  if (!imagePath) {
    return `
      <div class="preview-card">
        <div class="preview-image-frame preview-empty">
          <p class="helper-text">No preview image is available for this item.</p>
        </div>
        <div class="preview-caption">
          <div>
            <strong>${title}</strong>
            <p class="subtle">${subtitle}</p>
          </div>
        </div>
      </div>
    `;
  }

  return `
    <div class="preview-card">
      <div class="preview-image-frame">
        <img class="preview-image" src="${imagePath}" alt="${title}">
      </div>
      <div class="preview-caption">
        <div>
          <strong>${title}</strong>
          <p class="subtle">${subtitle}</p>
        </div>
      </div>
    </div>
  `;
}

function renderSourceOrProblemPreview(selected, mode) {
  const root = document.getElementById("previewSurface");
  const imagePath = mode === "source"
    ? (selected.sourceImagePath || selected.imagePath)
    : (selected.imagePath || selected.sourceImagePath);
  const subtitle = mode === "source"
    ? `Source page | ${selected.subject} | ${selected.sourcePageId || "page unknown"}`
    : `Problem crop | ${selected.subject} | start ${selected.startYPages.toFixed(1)}p`;
  root.innerHTML = renderImagePreview(selected.title, subtitle, imagePath);
}

function renderBoardPreview(placements, selected) {
  const template = getTemplate();
  const root = document.getElementById("previewSurface");
  const maxUsedPages = Math.max(
    template.baseSlotHeight * 4,
    ...placements.map((item) => item.snappedNextStartYPages + template.baseSlotHeight),
  );
  const heightScale = 136;
  const boardHeightPx = maxUsedPages * heightScale;

  const boardView = document.createElement("div");
  boardView.className = "board-view";
  boardView.innerHTML = `
    <div class="board-track" style="height:${boardHeightPx}px">
      <div class="board-writing-zone" style="width:${(1 - template.fixedLeftRatio) * 100}%">
        <span>writing space</span>
      </div>
    </div>
  `;

  const track = boardView.querySelector(".board-track");
  for (let y = 0; y <= maxUsedPages + 0.001; y += template.baseSlotHeight) {
    const line = document.createElement("div");
    line.className = "board-grid-line";
    line.style.top = `${y * heightScale + 24}px`;
    line.innerHTML = `<strong>${y.toFixed(1)}p</strong>`;
    track.appendChild(line);
  }

  placements.forEach((item) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `board-card${item.id === state.selectedId ? " is-selected" : ""}`;
    card.style.top = `${item.startYPages * heightScale + 24}px`;
    card.style.height = `${Math.max(112, item.actualHeightPages * heightScale - 12)}px`;
    card.style.width = `${template.fixedLeftRatio * 100 - 6}%`;
    card.innerHTML = `
      <div class="board-card-header">
        <strong>${item.title}</strong>
        <span>${item.subject}</span>
      </div>
      <img src="${item.imagePath}" alt="${item.title}">
    `;
    card.addEventListener("click", () => {
      state.selectedId = item.id;
      render();
    });
    track.appendChild(card);

    if (item.id === selected.id) {
      const snapLine = document.createElement("div");
      snapLine.className = "snap-line";
      snapLine.style.top = `${item.snappedNextStartYPages * heightScale + 24}px`;
      snapLine.innerHTML = `<span class="snap-label">next start ${item.snappedNextStartYPages.toFixed(1)}p</span>`;
      track.appendChild(snapLine);
    }
  });

  root.innerHTML = "";
  root.appendChild(boardView);

  if (selected.boardRenderPath) {
    const renderLink = document.createElement("div");
    renderLink.className = "board-render-actions";
    renderLink.innerHTML = `
      <a class="chip-button" href="${selected.boardRenderPath}" target="_blank" rel="noreferrer">
        Open rendered board image
      </a>
    `;
    root.appendChild(renderLink);
  }
}

function renderInspector(selected) {
  const root = document.getElementById("inspectorContent");
  const session = state.session;
  const warnings = [];
  if (selected.overflowViolation) {
    warnings.push("This problem is taller than 1.2p but overflow is currently disabled.");
  }
  if (selected.readingHeavy) {
    warnings.push("Reading-heavy mode keeps the content readable and snaps the next problem later.");
  }
  if (selected.snappedNextStartYPages - selected.startYPages > 2.4) {
    warnings.push("This item pushes later problems down significantly. Consider another lesson pack.");
  }

  root.innerHTML = `
    <div class="inspector-card">
      <h3>Session</h3>
      <p class="helper-text">${session.sessionName}</p>
      <div class="inspector-row">
        <label>Source</label>
        <span>${session.dataSource}</span>
      </div>
      <div class="inspector-row">
        <label>Generated</label>
        <span>${session.generatedAt || "manual"}</span>
      </div>
      <div class="inspector-links">
        ${session.edbFileUri ? `<a class="text-link" href="${session.edbFileUri}" target="_blank" rel="noreferrer">Open EDB</a>` : ""}
        ${selected.boardRenderPath ? `<a class="text-link" href="${selected.boardRenderPath}" target="_blank" rel="noreferrer">Open board render</a>` : ""}
        ${selected.sourceImagePath ? `<a class="text-link" href="${selected.sourceImagePath}" target="_blank" rel="noreferrer">Open source image</a>` : ""}
      </div>
    </div>

    <div class="inspector-card">
      <h3>Selected problem</h3>
      <p class="helper-text">${selected.title}</p>
      <div class="inspector-row">
        <label>Subject</label>
        <span>${selected.subject}</span>
      </div>
      <div class="inspector-row">
        <label>Source page</label>
        <span>${selected.sourcePageId || "unknown"}</span>
      </div>
      <div class="inspector-row">
        <label>Start</label>
        <span>${selected.startYPages.toFixed(1)}p</span>
      </div>
      <div class="inspector-row">
        <label>Next snapped start</label>
        <span>${selected.snappedNextStartYPages.toFixed(1)}p</span>
      </div>
    </div>

    <div class="inspector-card">
      <h3>Simple controls</h3>
      <div class="inspector-row">
        <label for="heightRange">Actual content height</label>
        <span id="heightOutput" class="range-output">${selected.actualHeightPages.toFixed(2)}p</span>
      </div>
      <input id="heightRange" type="range" min="0.6" max="2.4" step="0.02" value="${selected.actualHeightPages}">
      <div class="inspector-row">
        <label for="overflowToggle">Allow overflow</label>
        <input id="overflowToggle" type="checkbox" ${selected.overflowAllowed ? "checked" : ""}>
      </div>
      <div class="inspector-row">
        <label for="readingToggle">Reading-heavy</label>
        <input id="readingToggle" type="checkbox" ${selected.readingHeavy ? "checked" : ""}>
      </div>
    </div>

    <div class="inspector-card">
      <h3>Quick actions</h3>
      <div class="quick-actions">
        <button class="chip-button" id="moveUpButton" type="button">Move up</button>
        <button class="chip-button" id="moveDownButton" type="button">Move down</button>
        <button class="chip-button" id="duplicateButton" type="button">Duplicate</button>
        <button class="chip-button is-danger" id="deleteButton" type="button">Delete</button>
      </div>
    </div>

    <div class="inspector-card">
      <h3>Warnings</h3>
      <div class="warning-list">
        ${warnings.length ? warnings.map((item) => `<div class="warning-item">${item}</div>`).join("") : '<p class="helper-text">No blocking issues. Sequence can be exported.</p>'}
      </div>
    </div>
  `;

  root.querySelector("#heightRange").addEventListener("input", (event) => {
    const nextValue = Number(event.target.value);
    root.querySelector("#heightOutput").textContent = `${nextValue.toFixed(2)}p`;
    updateProblem(selected.id, { actualHeightPages: nextValue });
  });
  root.querySelector("#overflowToggle").addEventListener("change", (event) => {
    updateProblem(selected.id, { overflowAllowed: event.target.checked });
  });
  root.querySelector("#readingToggle").addEventListener("change", (event) => {
    updateProblem(selected.id, { readingHeavy: event.target.checked });
  });
  root.querySelector("#moveUpButton").addEventListener("click", () => moveProblem(selected.id, -1));
  root.querySelector("#moveDownButton").addEventListener("click", () => moveProblem(selected.id, 1));
  root.querySelector("#duplicateButton").addEventListener("click", () => duplicateProblem(selected.id));
  root.querySelector("#deleteButton").addEventListener("click", () => deleteProblem(selected.id));
}

function renderSummary(placements) {
  const overflowCount = placements.filter((item) => item.overflowAmountPages > 0).length;
  const maxBottom = Math.max(...placements.map((item) => item.actualBottomYPages));
  document.getElementById("problemCount").textContent = String(placements.length);
  document.getElementById("overflowCount").textContent = String(overflowCount);
  document.getElementById("boardUsage").textContent = `${maxBottom.toFixed(1)}p`;
}

function renderSessionHeader() {
  const sessionBadge = document.getElementById("sessionBadge");
  const edbStatus = document.getElementById("edbStatus");
  sessionBadge.textContent = `${state.session.dataSource} | ${state.session.sessionName}`;

  if (state.session.edbFileUri) {
    edbStatus.textContent = "open edb";
    edbStatus.href = state.session.edbFileUri;
    edbStatus.classList.remove("is-disabled");
  } else {
    edbStatus.textContent = "no edb";
    edbStatus.href = "#";
    edbStatus.classList.add("is-disabled");
  }
}

function render() {
  const placements = computePlacements();
  const selected = placements.find((item) => item.id === state.selectedId) || placements[0];
  updateRuntimeControls();
  if (!selected) {
    return;
  }

  document.querySelectorAll("[data-preview-mode]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.previewMode === state.previewMode);
  });
  document.getElementById("selectedSubject").textContent = selected.subject;
  document.getElementById("selectedPlacement").textContent = `start ${selected.startYPages.toFixed(1)}p | next ${selected.snappedNextStartYPages.toFixed(1)}p`;
  renderSessionHeader();

  if (state.previewMode === "source") {
    document.getElementById("previewTitle").textContent = "Source preview";
    document.getElementById("previewSubtitle").textContent = "Original page or screenshot feeding the current problem.";
    renderSourceOrProblemPreview(selected, "source");
  } else if (state.previewMode === "problem") {
    document.getElementById("previewTitle").textContent = "Problem preview";
    document.getElementById("previewSubtitle").textContent = "Cropped problem asset generated by the MVP export.";
    renderSourceOrProblemPreview(selected, "problem");
  } else {
    document.getElementById("previewTitle").textContent = "Board preview";
    document.getElementById("previewSubtitle").textContent = "Live staircase layout plus a link to the rendered board image.";
    renderBoardPreview(placements, selected);
  }

  renderFilmstrip(placements);
  renderInspector(selected);
  renderSummary(placements);
}

document.querySelectorAll("[data-preview-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    state.previewMode = button.dataset.previewMode;
    render();
  });
});

document.getElementById("templateSelect").addEventListener("change", (event) => {
  state.templateKey = event.target.value;
  render();
});

document.getElementById("addReadingHeavy").addEventListener("click", addLongPassage);

const sessionFileInput = document.getElementById("sessionFileInput");
document.getElementById("loadSessionButton").addEventListener("click", () => {
  sessionFileInput.click();
});

sessionFileInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) {
    return;
  }

  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    applySession(normalizeSession(parsed, file.name));
  } catch (error) {
    window.alert(`Failed to load session JSON: ${error.message}`);
  } finally {
    sessionFileInput.value = "";
  }
});

document.getElementById("useGeneratedButton").addEventListener("click", async () => {
  if (!state.apiAvailable) {
    window.location.reload();
    return;
  }
  try {
    applySession(await fetchLatestSessionFromApi());
    setRunStatus("Loaded latest session from local app server.", "success");
  } catch (error) {
    setRunStatus(`Failed to load latest session: ${error.message}`, "error");
  }
});

document.getElementById("useSampleButton").addEventListener("click", () => {
  applySession(sampleSession);
  setRunStatus("Switched to bundled sample data.", "neutral");
});

const sourceFileInput = document.getElementById("sourceFileInput");
document.getElementById("chooseSourceButton").addEventListener("click", () => {
  sourceFileInput.click();
});
sourceFileInput.addEventListener("change", (event) => {
  state.runSourceFile = event.target.files?.[0] || null;
  updateRuntimeControls();
});

document.getElementById("runExportButton").addEventListener("click", runExportFromApi);

async function initializeRuntimeConnection() {
  syncTemplateSelect();
  render();
  await probeApi();
  if (!state.apiAvailable) {
    return;
  }
  try {
    const latestSession = await fetchLatestSessionFromApi();
    applySession(latestSession);
    setRunStatus("Loaded latest session from local app server.", "success");
  } catch (error) {
    setRunStatus("Local app server is connected. Choose a source to create the first session.", "neutral");
  }
}

initializeRuntimeConnection();
