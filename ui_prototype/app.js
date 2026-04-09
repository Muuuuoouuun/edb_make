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
  {
    id: "math-05",
    title: "05. Sequence application",
    subject: "math",
    imagePath: "../out_images_sample4/record_0004_img_0.jpg",
    actualHeightPages: 1.08,
    overflowAllowed: false,
    readingHeavy: false,
  },
  {
    id: "korean-06",
    title: "06. Short literature item",
    subject: "korean",
    imagePath: "../out_images_sample4/record_0005_img_0.jpg",
    actualHeightPages: 0.98,
    overflowAllowed: true,
    readingHeavy: false,
  },
];

const state = {
  problems: (window.PROTOTYPE_DATA?.problems || fallbackProblems).map((item) => ({ ...item })),
  selectedId: (window.PROTOTYPE_DATA?.problems || fallbackProblems)[0].id,
  previewMode: "board",
  templateKey: "academy-default",
  dragId: null,
};

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

function getSelectedPlacement() {
  return computePlacements().find((item) => item.id === state.selectedId) || null;
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
    state.selectedId = state.problems[Math.max(0, index - 1)].id;
  }
  render();
}

function addLongPassage() {
  const nextId = `korean-${String(state.problems.length + 1).padStart(2, "0")}`;
  state.problems.push({
    id: nextId,
    title: "New long Korean passage",
    subject: "korean",
    imagePath: "../out_images_sample4/record_0006_img_0.jpg",
    actualHeightPages: 1.58,
    overflowAllowed: true,
    readingHeavy: true,
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

function renderSourceOrProblemPreview(selected, mode) {
  const root = document.getElementById("previewSurface");
  const label = mode === "source" ? "Source image" : "Problem crop";
  root.innerHTML = `
    <div class="preview-card">
      <div class="preview-image-frame">
        <img class="preview-image" src="${selected.imagePath}" alt="${selected.title}">
      </div>
      <div class="preview-caption">
        <div>
          <strong>${selected.title}</strong>
          <p class="subtle">${label} | ${selected.subject} | start ${selected.startYPages.toFixed(1)}p</p>
        </div>
        <div class="inline-meta">
          <span class="meta-pill">${selected.actualHeightPages.toFixed(2)}p actual</span>
          <span class="meta-pill">${selected.snappedNextStartYPages.toFixed(1)}p next</span>
        </div>
      </div>
    </div>
  `;
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
}

function renderInspector(selected) {
  const root = document.getElementById("inspectorContent");
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
      <h3>Selected problem</h3>
      <p class="helper-text">${selected.title}</p>
      <div class="inspector-row">
        <label>Subject</label>
        <span>${selected.subject}</span>
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

function render() {
  const placements = computePlacements();
  const selected = placements.find((item) => item.id === state.selectedId) || placements[0];
  if (!selected) {
    return;
  }

  document.querySelectorAll("[data-preview-mode]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.previewMode === state.previewMode);
  });
  document.getElementById("selectedSubject").textContent = selected.subject;
  document.getElementById("selectedPlacement").textContent = `start ${selected.startYPages.toFixed(1)}p | next ${selected.snappedNextStartYPages.toFixed(1)}p`;

  if (state.previewMode === "source") {
    document.getElementById("previewTitle").textContent = "Source preview";
    document.getElementById("previewSubtitle").textContent = "Uploaded page or image order check.";
    renderSourceOrProblemPreview(selected, "source");
  } else if (state.previewMode === "problem") {
    document.getElementById("previewTitle").textContent = "Problem preview";
    document.getElementById("previewSubtitle").textContent = "Cleaned problem card before board placement.";
    renderSourceOrProblemPreview(selected, "problem");
  } else {
    document.getElementById("previewTitle").textContent = "Board preview";
    document.getElementById("previewSubtitle").textContent = "1.2-page snapped staircase layout with writing space.";
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

render();
