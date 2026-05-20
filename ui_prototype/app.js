const BASE_SLOT_HEIGHT = 1.2;

const DEFAULT_OUTPUT_DIR = "mvp_export_app";
const DEFAULT_AI_FALLBACK = Object.freeze({
  enabled: false,
  mode: "off",
  provider: "gemini",
  model: "gemini-2.5-pro",
  threshold: 0.72,
  maxRegions: 30,
  timeoutMs: 18000,
  saveDebug: false,
  failOnError: false,
  provided: false,
});
const AI_PROVIDER_PRESETS = Object.freeze({
  gemini: {
    provider: "gemini",
    model: "gemini-2.5-pro",
    threshold: 0.72,
    maxRegions: 30,
    timeoutMs: 18000,
  },
});
const DEFAULT_INPUT_INTENT = "auto";
const INPUT_INTENTS = Object.freeze({
  auto: {
    label: "자동 판별",
    exportMode: "question",
    payload: "auto",
    payloadSnake: "auto",
  },
  "single-problem": {
    label: "한 문제",
    exportMode: "question",
    payload: "single-problem",
    payloadSnake: "single_problem",
  },
  "multi-problem": {
    label: "여러 문제",
    exportMode: "question",
    payload: "multi-problem",
    payloadSnake: "multi_problem",
  },
  "page-as-is": {
    label: "페이지 그대로",
    exportMode: "question",
    payload: "page-as-is",
    payloadSnake: "page_as_is",
  },
});
const SUPPORTED_SOURCE_EXTENSIONS = /\.(pdf|png|jpe?g|webp|bmp|tiff?|heic|heif)$/i;

const fallbackProblems = [
  {
    id: "math-01",
    title: "01. 함수 그래프 기초",
    subject: "math",
    imagePath: "../out_images_sample4/record_0000_img_0.jpg",
    actualHeightPages: 0.92,
    overflowAllowed: false,
    readingHeavy: false,
  },
  {
    id: "korean-02",
    title: "02. 긴 국어 지문",
    subject: "korean",
    imagePath: "../out_images_sample4/record_0001_img_0.jpg",
    actualHeightPages: 1.46,
    overflowAllowed: true,
    readingHeavy: true,
  },
  {
    id: "science-03",
    title: "03. 화학 개념 점검",
    subject: "science",
    imagePath: "../out_images_sample4/record_0002_img_0.jpg",
    actualHeightPages: 1.04,
    overflowAllowed: false,
    readingHeavy: false,
  },
  {
    id: "english-04",
    title: "04. 영어 독해 선택형",
    subject: "english",
    imagePath: "../out_images_sample4/record_0003_img_0.jpg",
    actualHeightPages: 1.34,
    overflowAllowed: true,
    readingHeavy: true,
  },
];

const templatePresets = {
  "academy-default": {
    name: "학원 기본형",
    baseSlotHeight: 1.2,
    boardPageCount: 50,
    fixedLeftRatio: 1 / 3,
  },
  "korean-reading": {
    name: "국어 지문형",
    baseSlotHeight: 1.2,
    boardPageCount: 50,
    fixedLeftRatio: 0.54,
  },
  "exam-review": {
    name: "시험 복습형",
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeTemplate(template) {
  if (!template) {
    return null;
  }
  return {
    name: template.name || "생성 세션",
    baseSlotHeight: toNumber(template.base_slot_height_pages ?? template.baseSlotHeight, BASE_SLOT_HEIGHT),
    boardPageCount: toNumber(template.board_page_count ?? template.boardPageCount, 50),
    fixedLeftRatio: toNumber(template.fixed_left_zone_ratio ?? template.fixedLeftRatio, 1 / 3),
  };
}

function normalizeAiFallbackConfig(rawConfig, rawSummary) {
  const config = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  const summary = rawSummary && typeof rawSummary === "object" ? rawSummary : {};
  const rawMode = String(config.mode ?? summary.mode ?? DEFAULT_AI_FALLBACK.mode).trim().toLowerCase();
  const mode = ["auto", "force", "off"].includes(rawMode) ? rawMode : DEFAULT_AI_FALLBACK.mode;
  const rawProvider = String(config.provider ?? summary.provider ?? DEFAULT_AI_FALLBACK.provider).trim().toLowerCase();
  // All AI traffic flows through Gemini now; legacy values are normalized.
  const provider = "gemini";
  void rawProvider;
  const defaultPreset = AI_PROVIDER_PRESETS.gemini;
  const model = String(config.model ?? summary.model ?? defaultPreset.model).trim() || defaultPreset.model;
  const threshold = Math.min(1, Math.max(0, toNumber(config.threshold ?? config.aiThreshold ?? summary.threshold, defaultPreset.threshold)));
  const maxRegions = Math.max(1, Math.round(toNumber(config.max_regions ?? config.maxRegions ?? summary.max_regions ?? summary.maxRegions, defaultPreset.maxRegions)));
  const timeoutMs = Math.max(1000, Math.round(toNumber(config.timeout_ms ?? config.timeoutMs ?? summary.timeout_ms ?? summary.timeoutMs, defaultPreset.timeoutMs)));
  const enabled = Boolean(config.enabled ?? summary.requested ?? (mode !== "off"));

  return {
    enabled,
    mode,
    provider,
    model,
    threshold,
    maxRegions,
    timeoutMs,
    saveDebug: Boolean(config.save_debug ?? config.saveDebug),
    failOnError: Boolean(config.fail_on_error ?? config.failOnError),
    provided: Boolean(rawConfig || rawSummary),
  };
}

function normalizeAiSummary(rawSummary) {
  if (!rawSummary || typeof rawSummary !== "object") {
    return {
      requested: false,
      mode: DEFAULT_AI_FALLBACK.mode,
      provider: DEFAULT_AI_FALLBACK.provider,
      model: DEFAULT_AI_FALLBACK.model,
      attemptedPageCount: 0,
      appliedPageCount: 0,
      statusCounts: {},
      provided: false,
    };
  }

  const rawStatusCounts = rawSummary.status_counts || rawSummary.statusCounts || {};
  const statusCounts = Object.entries(rawStatusCounts).reduce((accumulator, [status, count]) => {
    const numeric = Number(count);
    if (status && Number.isFinite(numeric) && numeric >= 0) {
      accumulator[String(status)] = numeric;
    }
    return accumulator;
  }, {});

  return {
    requested: Boolean(rawSummary.requested),
    mode: String(rawSummary.mode || DEFAULT_AI_FALLBACK.mode).trim().toLowerCase() || DEFAULT_AI_FALLBACK.mode,
    provider: String(rawSummary.provider || DEFAULT_AI_FALLBACK.provider).trim().toLowerCase() || DEFAULT_AI_FALLBACK.provider,
    model: String(rawSummary.model || DEFAULT_AI_FALLBACK.model).trim() || DEFAULT_AI_FALLBACK.model,
    attemptedPageCount: Math.max(0, Math.round(toNumber(rawSummary.attempted_page_count ?? rawSummary.attemptedPageCount, 0))),
    appliedPageCount: Math.max(0, Math.round(toNumber(rawSummary.applied_page_count ?? rawSummary.appliedPageCount, 0))),
    statusCounts,
    provided: true,
  };
}

function normalizeProblemNumber(value) {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null;
}

function parseProblemTitle(rawTitle) {
  const markerMatch = rawTitle.match(/^\s*(?:문항\s*)?([1-9][0-9]{0,2})(?:[.)])(?:\s+|$)/);
  if (markerMatch) {
    return {
      problemNumber: normalizeProblemNumber(markerMatch[1]),
      cleanedTitle: rawTitle.slice(markerMatch[0].length).trim(),
    };
  }

  const labeledMatch = rawTitle.match(/^\s*문항\s*([1-9][0-9]{0,2})(?:\s*[·:\-].*)?$/);
  if (labeledMatch) {
    return {
      problemNumber: normalizeProblemNumber(labeledMatch[1]),
      cleanedTitle: "",
    };
  }

  const genericProblemMatch = rawTitle.match(/(?:^|[\s_-])problem\s*(\d+)$/i);
  if (genericProblemMatch) {
    return {
      problemNumber: normalizeProblemNumber(genericProblemMatch[1]),
      cleanedTitle: "",
    };
  }

  return {
    problemNumber: null,
    cleanedTitle: rawTitle.trim(),
  };
}

function getProblemLabel(problem, fallbackIndex = null) {
  const problemNumber = normalizeProblemNumber(problem?.problemNumber);
  if (problemNumber !== null) {
    return `문항 ${problemNumber}`;
  }
  if (Number.isInteger(fallbackIndex)) {
    return `문항 ${fallbackIndex + 1}`;
  }
  return (problem?.title || "").trim() || "문항";
}

function getProblemHeading(problem, fallbackIndex = null) {
  const title = (problem?.title || "").trim();
  const problemNumber = normalizeProblemNumber(problem?.problemNumber);
  if (problemNumber === null) {
    return title || getProblemLabel(problem, fallbackIndex);
  }
  const label = `문항 ${problemNumber}`;
  if (!title || title === label) {
    return label;
  }
  return `${label} · ${title}`;
}

function normalizeProblem(problem, index) {
  const rawTitle = (problem.title || "").trim();
  const parsedTitle = parseProblemTitle(rawTitle);
  const problemNumber = normalizeProblemNumber(problem.problemNumber ?? problem.problem_number) ?? parsedTitle.problemNumber;
  const normalizedTitle = parsedTitle.cleanedTitle || (problemNumber !== null ? `문항 ${problemNumber}` : `문항 ${index + 1}`);
  return {
    id: problem.id || problem.problem_id || `problem-${String(index + 1).padStart(2, "0")}`,
    title: normalizedTitle,
    problemNumber,
    subject: problem.subject || "unknown",
    imagePath: normalizePath(problem.imagePath || problem.image_path || problem.sourceImagePath || ""),
    sourceImagePath: normalizePath(problem.sourceImagePath || problem.source_image_path || problem.imagePath || ""),
    sourceFileName: problem.sourceFileName || problem.source_file_name || "",
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
    recordMode: problem.recordMode || problem.record_mode || "",
    textRecordCount: toNumber(problem.textRecordCount ?? problem.text_record_count, 0),
    imageRecordCount: toNumber(problem.imageRecordCount ?? problem.image_record_count, 0),
    riskFlags: Array.isArray(problem.riskFlags || problem.risk_flags)
      ? (problem.riskFlags || problem.risk_flags)
      : [],
    excluded: Boolean(problem.excluded ?? problem.isExcluded),
  };
}

function normalizeSession(rawSession, fallbackName = "불러온 세션") {
  const rawProblems = Array.isArray(rawSession?.problems) ? rawSession.problems : [];
  const aiSummary = normalizeAiSummary(rawSession?.ai_summary || rawSession?.aiSummary);
  const aiFallback = normalizeAiFallbackConfig(rawSession?.ai_fallback || rawSession?.aiFallback, rawSession?.ai_summary || rawSession?.aiSummary);
  const exportMode = rawSession?.export_mode || rawSession?.exportMode || "question";
  const rawInputIntent = rawSession?.input_intent || rawSession?.inputIntent || "";
  return {
    sessionName: rawSession?.session_name || rawSession?.sessionName || fallbackName,
    dataSource: rawSession?.data_source || rawSession?.dataSource || "manual",
    generatedAt: rawSession?.generated_at || rawSession?.generatedAt || "",
    outputDir: rawSession?.output_dir || rawSession?.outputDir || "",
    sourceMode: rawSession?.source_mode || rawSession?.sourceMode || "single",
    inputIntent: rawInputIntent || (exportMode === "page" ? "page-as-is" : "auto"),
    inputNotes: rawSession?.input_notes || rawSession?.inputNotes || "",
    exportMode,
    recordMode: rawSession?.record_mode || rawSession?.recordMode || "mixed",
    inputFileCount: toNumber(rawSession?.input_file_count ?? rawSession?.inputFileCount, 0),
    sourcePageCount: toNumber(rawSession?.source_page_count ?? rawSession?.sourcePageCount, 0),
    detectedProblemCount: toNumber(rawSession?.detected_problem_count ?? rawSession?.detectedProblemCount, rawProblems.length),
    inputFiles: Array.isArray(rawSession?.input_files || rawSession?.inputFiles)
      ? (rawSession?.input_files || rawSession?.inputFiles)
      : [],
    pagesJsonPath: rawSession?.pages_json_path || rawSession?.pagesJsonPath || "",
    placementsJsonPath: rawSession?.placements_json_path || rawSession?.placementsJsonPath || "",
    edbPath: rawSession?.edb_path || rawSession?.edbPath || "",
    edbFileUri: normalizePath(rawSession?.edb_file_uri || rawSession?.edbFileUri || rawSession?.edb_path || ""),
    pages: Array.isArray(rawSession?.pages) ? rawSession.pages : [],
    renderedPageFileUris: (rawSession?.rendered_page_file_uris || rawSession?.renderedPageFileUris || rawSession?.rendered_page_paths || rawSession?.renderedPagePaths || []).map(normalizePath),
    template: normalizeTemplate(rawSession?.template),
    warningMessages: Array.isArray(rawSession?.warning_messages || rawSession?.warningMessages)
      ? (rawSession?.warning_messages || rawSession?.warningMessages)
      : [],
    aiFallback,
    aiSummary,
    problems: rawProblems.map(normalizeProblem),
  };
}

function cloneProblems(problems) {
  return problems.map((problem) => ({ ...problem }));
}

function fileKey(file) {
  return [file.name, file.size, file.lastModified].join("::");
}

function isPdfFile(file) {
  return /\.pdf$/i.test(file.name || "");
}

function isSupportedSourceFile(file) {
  if (!file) {
    return false;
  }
  const type = String(file.type || "").toLowerCase();
  return type.startsWith("image/") || type === "application/pdf" || SUPPORTED_SOURCE_EXTENSIONS.test(file.name || "");
}

function clipboardFileName(file, index) {
  if (file.name) {
    return file.name;
  }
  const type = String(file.type || "").toLowerCase();
  const extension = type === "application/pdf"
    ? "pdf"
    : type.includes("jpeg")
      ? "jpg"
      : type.includes("webp")
        ? "webp"
        : type.includes("bmp")
          ? "bmp"
          : type.includes("tiff")
            ? "tiff"
            : "png";
  return `clipboard-${Date.now()}-${index + 1}.${extension}`;
}

function normalizeClipboardFile(file, index) {
  if (!file || file.name) {
    return file;
  }
  return new File([file], clipboardFileName(file, index), {
    type: file.type || "image/png",
    lastModified: Date.now(),
  });
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0KB";
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

function subjectLabel(subject) {
  const labels = {
    unknown: "자동",
    math: "수학",
    science: "과학",
    korean: "국어",
    english: "영어",
    social: "사회",
  };
  return labels[subject] || subject || "자동";
}

function sessionSourceLabel(source) {
  const labels = {
    sample: "샘플",
    manual: "수동",
    build_mvp_export: "자동 파싱",
    question_export: "문항 파싱",
  };
  return labels[source] || source || "수동";
}

function exportModeLabel(mode) {
  return mode === "page" ? "페이지별" : "문항별";
}

function normalizeInputIntent(value) {
  const normalized = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  return Object.prototype.hasOwnProperty.call(INPUT_INTENTS, normalized) ? normalized : DEFAULT_INPUT_INTENT;
}

function inputIntentLabel(intent) {
  return INPUT_INTENTS[normalizeInputIntent(intent)].label;
}

function inputIntentExportMode(intent) {
  return INPUT_INTENTS[normalizeInputIntent(intent)].exportMode;
}

function readInputIntent() {
  const selected = document.querySelector('input[name="inputIntent"]:checked');
  return normalizeInputIntent(selected?.value || state.inputIntent || DEFAULT_INPUT_INTENT);
}

function syncInputIntentControls({ syncLayout = true } = {}) {
  const intent = normalizeInputIntent(state.inputIntent || DEFAULT_INPUT_INTENT);
  const selected = document.querySelector(`input[name="inputIntent"][value="${intent}"]`);
  if (selected) {
    selected.checked = true;
  }

  const layoutModeSelect = document.getElementById("runLayoutModeSelect");
  if (syncLayout && layoutModeSelect) {
    layoutModeSelect.value = inputIntentExportMode(intent);
  }
}

function aiProviderLabel(provider) {
  // Legacy provider tags (claude/openai) all resolve to Gemini in the backend.
  void provider;
  return "Gemini";
}

function aiModeLabel(mode) {
  if (mode === "force") {
    return "강제";
  }
  if (mode === "auto") {
    return "자동";
  }
  return "꺼짐";
}

function aiStatusLabel(status) {
  const labels = {
    applied: "적용됨",
    applied_with_warnings: "적용됨",
    disabled: "비활성",
    missing_api_key: "키 없음",
    not_needed: "불필요",
    provider_pending: "제미나이 대기",
    repair_error: "요청 실패",
    skipped: "건너뜀",
    too_many_blocks: "블록 과다",
    unknown: "알 수 없음",
  };
  return labels[status] || status || "알 수 없음";
}

function formatAiStatusCounts(statusCounts) {
  const entries = Object.entries(statusCounts || {}).filter(([, count]) => Number(count) > 0);
  if (!entries.length) {
    return "";
  }
  return entries.map(([status, count]) => `${aiStatusLabel(status)} ${count}p`).join(" · ");
}

function getAiControlElements() {
  return {
    enabled: document.getElementById("runAiFallbackEnabled"),
    mode: document.getElementById("runAiFallbackModeSelect"),
    provider: document.getElementById("runAiProviderSelect"),
    model: document.getElementById("runAiModelInput"),
    threshold: document.getElementById("runAiThresholdInput"),
    maxRegions: document.getElementById("runAiMaxRegionsInput"),
    timeoutMs: document.getElementById("runAiTimeoutInput"),
    saveDebug: document.getElementById("runAiSaveDebug"),
    openAiPresetButton: document.getElementById("applyOpenAiPresetButton"),
    geminiPresetButton: document.getElementById("applyGeminiPresetButton"),
    helper: document.getElementById("aiFallbackHelper"),
  };
}

function readAiFallbackForm() {
  const controls = getAiControlElements();
  const provider = "gemini";
  const preset = AI_PROVIDER_PRESETS.gemini;
  const requestedMode = String(controls.mode?.value || DEFAULT_AI_FALLBACK.mode).trim().toLowerCase();
  const mode = controls.enabled?.checked ? requestedMode : "off";

  return {
    enabled: mode !== "off",
    mode: ["auto", "force", "off"].includes(mode) ? mode : "off",
    provider,
    model: (controls.model?.value || "").trim() || preset.model,
    threshold: Math.min(1, Math.max(0, toNumber(controls.threshold?.value, preset.threshold))),
    maxRegions: Math.max(1, Math.round(toNumber(controls.maxRegions?.value, preset.maxRegions))),
    timeoutMs: Math.max(1000, Math.round(toNumber(controls.timeoutMs?.value, preset.timeoutMs))),
    saveDebug: Boolean(controls.saveDebug?.checked),
  };
}

function aiFallbackForInputIntent(inputIntent, config) {
  const normalizedIntent = normalizeInputIntent(inputIntent);
  const preset = AI_PROVIDER_PRESETS.gemini;
  if (normalizedIntent === "multi-problem" && (!config.enabled || config.mode === "off")) {
    return {
      ...config,
      enabled: true,
      mode: "auto",
      provider: "gemini",
      model: config.model || preset.model,
      threshold: config.threshold || preset.threshold,
      maxRegions: config.maxRegions || preset.maxRegions,
      timeoutMs: config.timeoutMs || preset.timeoutMs,
    };
  }
  if (normalizedIntent === "single-problem" || normalizedIntent === "page-as-is") {
    return {
      ...config,
      enabled: false,
      mode: "off",
    };
  }
  return config;
}

function writeAiFallbackForm(config) {
  const controls = getAiControlElements();
  const normalized = normalizeAiFallbackConfig(config, null);
  if (controls.enabled) {
    controls.enabled.checked = normalized.enabled;
  }
  if (controls.mode) {
    controls.mode.value = normalized.mode;
  }
  if (controls.provider) {
    controls.provider.value = normalized.provider;
  }
  if (controls.model) {
    controls.model.value = normalized.model;
  }
  if (controls.threshold) {
    controls.threshold.value = String(normalized.threshold);
  }
  if (controls.maxRegions) {
    controls.maxRegions.value = String(normalized.maxRegions);
  }
  if (controls.timeoutMs) {
    controls.timeoutMs.value = String(normalized.timeoutMs);
  }
  if (controls.saveDebug) {
    controls.saveDebug.checked = normalized.saveDebug;
  }
}

function syncAiFallbackControls() {
  const controls = getAiControlElements();
  const helper = controls.helper;
  if (!controls.enabled || !controls.mode || !controls.provider || !controls.model) {
    return;
  }

  const isEnabled = controls.enabled.checked;
  const isLocked = state.runBusy;
  [
    controls.mode,
    controls.provider,
    controls.model,
    controls.threshold,
    controls.maxRegions,
    controls.timeoutMs,
    controls.saveDebug,
  ].forEach((node) => {
    if (node) {
      node.disabled = isLocked || !isEnabled;
    }
  });
  controls.enabled.disabled = isLocked;
  [controls.openAiPresetButton, controls.geminiPresetButton].forEach((button) => {
    if (button) {
      button.disabled = isLocked;
    }
  });

  if (!helper) {
    return;
  }

  const config = readAiFallbackForm();
  helper.classList.remove("is-warning", "is-success");

  if (isLocked) {
    helper.textContent = "AI settings are locked while export is running.";
    return;
  }

  if (!isEnabled || config.mode === "off") {
    helper.textContent = "AI 보정이 꺼져 있습니다.";
    return;
  }

  if (config.provider === "gemini") {
    if (config.mode === "force") {
      helper.textContent = `${aiProviderLabel(config.provider)} ${config.model}로 모든 후보 페이지를 보정 시도합니다.`;
    } else {
      helper.textContent = `${aiProviderLabel(config.provider)} ${config.model}로 애매한 페이지만 선택 보정합니다.`;
    }
    helper.classList.add("is-success");
    return;
  }
}

function applyAiPreset(provider) {
  // Only one provider remains; the legacy argument is preserved for callers
  // that still pass "openai" or "claude".
  void provider;
  const normalizedProvider = "gemini";
  const preset = AI_PROVIDER_PRESETS.gemini;
  writeAiFallbackForm({
    enabled: true,
    mode: "auto",
    provider: preset.provider,
    model: preset.model,
    threshold: preset.threshold,
    maxRegions: preset.maxRegions,
    timeoutMs: preset.timeoutMs,
    saveDebug: false,
  });
  syncAiFallbackControls();
}

function summarizeQueuedSources() {
  if (!state.runSourceFiles.length) {
    return "선택된 소스 없음";
  }
  if (state.runSourceFiles.length === 1) {
    return state.runSourceFiles[0].name;
  }
  return `${state.runSourceFiles.length}개 파일 선택됨`;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const commaIndex = result.indexOf(",");
      resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error("파일을 읽지 못했습니다."));
    reader.readAsDataURL(file);
  });
}

function renderSourceQueue() {
  const root = document.getElementById("sourceQueue");
  if (!root) {
    return;
  }

  root.innerHTML = "";
  if (!state.runSourceFiles.length) {
    root.innerHTML = `
      <div class="source-queue-empty">
        <p class="helper-text">아직 업로드한 파일이 없습니다.</p>
      </div>
    `;
    return;
  }

  state.runSourceFiles.forEach((file, index) => {
    const card = document.createElement("article");
    card.className = "source-chip";
    const fileName = escapeHtml(file.name);
    card.innerHTML = `
      <div class="source-chip-head">
        <div>
          <div class="source-chip-name">${fileName}</div>
          <div class="source-chip-meta">
            <span>${index + 1}번째</span>
            <span>${isPdfFile(file) ? "PDF" : "이미지"}</span>
            <span>${formatFileSize(file.size)}</span>
          </div>
        </div>
        <span class="meta-pill">${isPdfFile(file) ? "PDF" : "사진"}</span>
      </div>
      <button class="small-button source-chip-remove" type="button" data-remove-index="${index}">제거</button>
    `;
    root.appendChild(card);
  });

  root.querySelectorAll("[data-remove-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const removeIndex = Number(button.dataset.removeIndex);
      state.runSourceFiles = state.runSourceFiles.filter((_, index) => index !== removeIndex);
      updateRuntimeControls();
      renderSourceQueue();
    });
  });
}

function updateQueuedFiles(fileList, { replace = false } = {}) {
  const rawFiles = Array.from(fileList || []).filter(Boolean);
  const incomingFiles = rawFiles.filter(isSupportedSourceFile);
  if (!incomingFiles.length) {
    if (rawFiles.length) {
      setRunStatus("지원하는 이미지/PDF 파일만 업로드 큐에 넣을 수 있습니다.", "warning");
    }
    return 0;
  }

  const incomingMap = new Map(incomingFiles.map((file) => [fileKey(file), file]));
  const incomingUniqueFiles = Array.from(incomingMap.values());
  const nextFiles = replace
    ? incomingUniqueFiles
    : [...state.runSourceFiles.filter((file) => !incomingMap.has(fileKey(file))), ...incomingUniqueFiles];

  state.runSourceFiles = nextFiles;
  updateRuntimeControls();
  renderSourceQueue();
  return incomingUniqueFiles.length;
}

function clearQueuedFiles() {
  state.runSourceFiles = [];
  updateRuntimeControls();
  renderSourceQueue();
}

function readPastedText() {
  const input = document.getElementById("pastedTextInput");
  return (input?.value || "").trim();
}

function writePastedText(value) {
  const input = document.getElementById("pastedTextInput");
  const text = String(value || "");
  state.pastedText = text;
  if (input && input.value !== text) {
    input.value = text;
  }
  updateRuntimeControls();
}

function clearPastedText() {
  writePastedText("");
  setRunStatus("붙여넣은 텍스트를 지웠습니다.", "neutral");
}

function clearAllState() {
  if (state.runBusy) {
    window.alert("현재 파싱 중에는 전체 초기화를 할 수 없습니다.");
    return;
  }

  const confirmed = window.confirm("현재 업로드 큐와 파싱 결과를 모두 지울까요?");
  if (!confirmed) {
    return;
  }

  clearQueuedFiles();
  state.previewMode = "problem";
  state.templateKey = "academy-default";
  state.dragId = null;
  state.composerOpen = false;
  resetRunOptions();

  document.getElementById("sourceFileInput").value = "";
  document.getElementById("cameraFileInput").value = "";
  document.getElementById("sessionFileInput").value = "";
  writePastedText("");

  applySession(createEmptySession());
  setRunStatus("업로드 큐와 현재 파싱 결과를 모두 초기화했습니다.", "neutral");
}

async function maybeAutoRun() {
  if (!state.autoParse || !state.apiAvailable || !state.runSourceFiles.length || state.runBusy) {
    return;
  }
  await runExportFromApi();
}

const sampleSession = normalizeSession(
  {
    session_name: "프로토타입 샘플",
    data_source: "sample",
    problems: window.PROTOTYPE_DATA?.problems || fallbackProblems,
  },
  "프로토타입 샘플",
);

function createEmptySession() {
  return normalizeSession(
    {
      session_name: "빈 세션",
      data_source: "manual",
      export_mode: "question",
      record_mode: "mixed",
      input_file_count: 0,
      source_page_count: 0,
      detected_problem_count: 0,
      input_files: [],
      warning_messages: [],
      problems: [],
    },
    "빈 세션",
  );
}

const generatedSession = window.EDB_UI_SESSION
  ? normalizeSession(window.EDB_UI_SESSION, "생성된 세션")
  : null;

const state = {
  session: generatedSession || sampleSession,
  problems: cloneProblems((generatedSession || sampleSession).problems),
  selectedId: (generatedSession || sampleSession).problems[0]?.id || null,
  previewMode: "problem",
  templateKey: "academy-default",
  dragId: null,
  composerOpen: false,
  apiAvailable: false,
  runBusy: false,
  autoParse: false,
  inputIntent: DEFAULT_INPUT_INTENT,
  pastedText: "",
  runSourceFiles: [],
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
      option.textContent = `${sessionTemplate.name} (세션)`;
      select.prepend(option);
    } else {
      existingGeneratedOption.textContent = `${sessionTemplate.name} (세션)`;
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
  state.selectedId = session.problems.find((problem) => !problem.excluded)?.id || session.problems[0]?.id || null;
  state.composerOpen = false;
  const layoutModeSelect = document.getElementById("runLayoutModeSelect");
  if (layoutModeSelect) {
    layoutModeSelect.value = session.exportMode || "question";
  }
  state.inputIntent = normalizeInputIntent(session.inputIntent || (session.exportMode === "page" ? "page-as-is" : DEFAULT_INPUT_INTENT));
  syncInputIntentControls({ syncLayout: false });
  writeAiFallbackForm(session.aiFallback);
  syncAiFallbackControls();
  syncTemplateSelect();
  render();
}

function resetRunOptions() {
  const runLayoutModeSelect = document.getElementById("runLayoutModeSelect");
  const runSubjectSelect = document.getElementById("runSubjectSelect");
  const runOcrSelect = document.getElementById("runOcrSelect");
  const outputDirInput = document.getElementById("outputDirInput");
  const runExportEdb = document.getElementById("runExportEdb");
  const autoParseToggle = document.getElementById("autoParseToggle");

  if (runLayoutModeSelect) {
    runLayoutModeSelect.value = "question";
  }
  if (runSubjectSelect) {
    runSubjectSelect.value = "unknown";
  }
  if (runOcrSelect) {
    runOcrSelect.value = "auto";
  }
  if (outputDirInput) {
    outputDirInput.value = DEFAULT_OUTPUT_DIR;
  }
  if (runExportEdb) {
    runExportEdb.checked = true;
  }

  state.inputIntent = DEFAULT_INPUT_INTENT;
  syncInputIntentControls();
  writePastedText("");
  state.autoParse = false;
  if (autoParseToggle) {
    autoParseToggle.checked = false;
  }
  writeAiFallbackForm(DEFAULT_AI_FALLBACK);
  syncAiFallbackControls();
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

function activeProblems() {
  return state.problems.filter((problem) => !problem.excluded);
}

function ensureSelectedProblem(placements) {
  if (!placements.length) {
    state.selectedId = null;
    return null;
  }
  const selected = placements.find((item) => item.id === state.selectedId);
  if (selected) {
    return selected;
  }
  state.selectedId = placements[0].id;
  return placements[0];
}

function computePlacements() {
  const template = getTemplate();
  let cursor = 0;

  return activeProblems().map((problem) => {
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
    title: `${source.title} 복제본`,
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

function toggleProblemExcluded(problemId) {
  const target = state.problems.find((item) => item.id === problemId);
  if (!target) {
    return;
  }
  target.excluded = !target.excluded;
  if (target.excluded && state.selectedId === problemId) {
    state.selectedId = activeProblems()[0]?.id || null;
  } else if (!target.excluded && !state.selectedId) {
    state.selectedId = target.id;
  }
  render();
}

function addLongPassage() {
  const nextId = `korean-${String(state.problems.length + 1).padStart(2, "0")}`;
  state.problems.push({
    id: nextId,
    title: "새 긴 국어 지문",
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

function openComposerModal() {
  state.composerOpen = true;
  renderComposerModal();
}

function closeComposerModal() {
  state.composerOpen = false;
  renderComposerModal();
}

function setRunStatus(message, tone = "neutral") {
  const node = document.getElementById("runStatusText");
  node.textContent = message;
  node.dataset.tone = tone;
}

function updateRuntimeControls() {
  const apiStatus = document.getElementById("apiStatus");
  const selectedSourceName = document.getElementById("selectedSourceName");
  const sourceQueueCount = document.getElementById("sourceQueueCount");
  const runExportButton = document.getElementById("runExportButton");
  const clearAllButton = document.getElementById("clearAllButton");
  const autoParseToggle = document.getElementById("autoParseToggle");
  const runLayoutModeSelect = document.getElementById("runLayoutModeSelect");
  const pasteHelper = document.getElementById("pasteHelper");
  const clearPastedTextButton = document.getElementById("clearPastedTextButton");
  const pastedTextInput = document.getElementById("pastedTextInput");
  const focusPasteTextButton = document.getElementById("focusPasteTextButton");
  const inputIntent = normalizeInputIntent(state.inputIntent);
  const textLength = readPastedText().length;

  apiStatus.textContent = state.apiAvailable ? "로컬 앱 연결됨" : "오프라인 미리보기";
  apiStatus.classList.toggle("is-connected", state.apiAvailable);
  apiStatus.classList.toggle("is-offline", !state.apiAvailable);

  selectedSourceName.textContent = summarizeQueuedSources();
  sourceQueueCount.textContent = state.runSourceFiles.length
    ? `${state.runSourceFiles.length}개 대기`
    : "0개 대기";
  runExportButton.disabled = !state.apiAvailable || state.runBusy || !state.runSourceFiles.length;
  if (clearAllButton) {
    clearAllButton.disabled = state.runBusy;
  }
  const exportMode = inputIntentExportMode(inputIntent) || runLayoutModeSelect?.value || "question";
  runExportButton.textContent = inputIntent === "auto"
    ? `${exportModeLabel(exportMode)} 자동 판별`
    : `${inputIntentLabel(inputIntent)} 변환`;
  autoParseToggle.checked = state.autoParse;
  if (pasteHelper) {
    pasteHelper.textContent = textLength
      ? `붙여넣은 텍스트 ${textLength.toLocaleString()}자 포함`
      : "이미지 복사 후 이 화면에서 Ctrl/Cmd+V";
    pasteHelper.classList.toggle("is-success", textLength > 0);
  }
  if (clearPastedTextButton) {
    clearPastedTextButton.disabled = state.runBusy || textLength === 0;
  }
  if (pastedTextInput) {
    pastedTextInput.disabled = state.runBusy;
  }
  if (focusPasteTextButton) {
    focusPasteTextButton.disabled = state.runBusy;
  }
  document.querySelectorAll('input[name="inputIntent"]').forEach((input) => {
    input.disabled = state.runBusy;
  });
  syncAiFallbackControls();
}

async function probeApi() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error(`health ${response.status}`);
    }
    state.apiAvailable = true;
    setRunStatus("로컬 파싱 API에 연결되었습니다. 사진을 올리면 바로 실행할 수 있습니다.", "success");
  } catch (error) {
    state.apiAvailable = false;
    setRunStatus("정적 미리보기 모드입니다. `app_server.py`를 실행하면 브라우저에서 바로 파싱할 수 있습니다.", "warning");
  }
  updateRuntimeControls();
}

function renderUserSettings(settings) {
  const statusBadge = document.getElementById("geminiKeyStatus");
  const helper = document.getElementById("geminiKeyHelper");
  const input = document.getElementById("geminiApiKeyInput");
  const clearButton = document.getElementById("clearGeminiKeyButton");
  if (!statusBadge || !helper) {
    return;
  }
  const hasKey = Boolean(settings?.hasGeminiApiKey);
  const preview = settings?.geminiApiKeyPreview || "";
  const source = settings?.geminiApiKeySource || "none";
  statusBadge.textContent = hasKey ? `키 설정됨 ${preview}` : "키 미설정";
  statusBadge.classList.toggle("is-connected", hasKey);
  statusBadge.classList.toggle("is-offline", !hasKey);
  if (clearButton) {
    clearButton.disabled = !hasKey;
  }
  if (input) {
    input.placeholder = hasKey ? `현재 ${preview} (새 키로 덮어쓰기)` : "AIza...";
  }
  if (!hasKey) {
    helper.textContent = "키가 없으면 AI 보정과 Gemini OCR 에스컬레이션이 비활성화됩니다.";
    helper.classList.remove("is-success", "is-warning");
    helper.classList.add("is-warning");
  } else if (source === "env") {
    helper.textContent = "셸 환경변수의 GEMINI_API_KEY를 사용 중입니다. 저장하면 그 값이 우선합니다.";
    helper.classList.remove("is-warning", "is-success");
  } else {
    helper.textContent = "저장된 키를 GEMINI_API_KEY로 자동 적용 중입니다.";
    helper.classList.remove("is-warning");
    helper.classList.add("is-success");
  }
}

async function fetchUserSettings() {
  try {
    const response = await fetch("/api/user-settings");
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `사용자 설정을 불러오지 못했습니다 (${response.status})`);
    }
    renderUserSettings(payload.settings);
  } catch (error) {
    const helper = document.getElementById("geminiKeyHelper");
    if (helper) {
      helper.textContent = `사용자 설정을 불러오지 못했습니다: ${error.message}`;
      helper.classList.add("is-warning");
    }
  }
}

async function saveGeminiApiKey(rawValue) {
  const helper = document.getElementById("geminiKeyHelper");
  const trimmed = (rawValue || "").trim();
  try {
    const response = await fetch("/api/user-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ geminiApiKey: trimmed }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `저장에 실패했습니다 (${response.status})`);
    }
    renderUserSettings(payload.settings);
    if (helper) {
      helper.textContent = trimmed ? "키를 저장하고 GEMINI_API_KEY로 적용했습니다." : "키를 삭제했습니다.";
      helper.classList.remove("is-warning");
      helper.classList.add("is-success");
    }
    const input = document.getElementById("geminiApiKeyInput");
    if (input) {
      input.value = "";
      input.type = "password";
    }
    const toggle = document.getElementById("toggleGeminiKeyVisibility");
    if (toggle) {
      toggle.setAttribute("aria-pressed", "false");
      toggle.textContent = "보기";
    }
  } catch (error) {
    if (helper) {
      helper.textContent = `저장 실패: ${error.message}`;
      helper.classList.remove("is-success");
      helper.classList.add("is-warning");
    }
  }
}

async function fetchLatestSessionFromApi() {
  const response = await fetch("/api/session/latest");
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `최근 세션을 불러오지 못했습니다 (${response.status})`);
  }
  return normalizeSession(payload.session, "최근 세션");
}

function sessionHasReviewPages(session) {
  return Array.isArray(session?.pages) && session.pages.length > 0;
}

function sessionRiskCount(session) {
  const problemRiskCount = (session?.problems || []).filter((problem) => (
    Array.isArray(problem.riskFlags) && problem.riskFlags.length > 0
  )).length;
  const pageRiskCount = (session?.pages || []).filter((page) => {
    const flags = page?.riskFlags || page?.risk_flags || [];
    return Array.isArray(flags) && flags.length > 0;
  }).length;
  const warningCount = Array.isArray(session?.warningMessages) ? session.warningMessages.length : 0;
  return problemRiskCount + pageRiskCount + warningCount;
}

function shouldRouteToReview(session, inputIntent) {
  if (!sessionHasReviewPages(session)) {
    return false;
  }
  const detectedCount = session.detectedProblemCount || session.problems?.length || 0;
  const isMulti = inputIntent === "multi-problem" || detectedCount > 1 || session.inputFileCount > 1;
  return isMulti || sessionRiskCount(session) > 0;
}

function postParseRoute(session, inputIntent) {
  return shouldRouteToReview(session, inputIntent) ? "/board.html?view=review" : "/board.html";
}

async function runExportFromApi() {
  if (!state.apiAvailable) {
    window.alert("로컬 파싱 API가 연결되지 않았습니다. 먼저 `app_server.py`를 실행해주세요.");
    return;
  }
  if (!state.runSourceFiles.length) {
    window.alert("먼저 사진이나 PDF를 선택해주세요.");
    return;
  }

  const runExportButton = document.getElementById("runExportButton");
  try {
    state.runBusy = true;
    runExportButton.disabled = true;
    const inputIntent = readInputIntent();
    const inputIntentConfig = INPUT_INTENTS[inputIntent];
    const exportMode = inputIntentExportMode(inputIntent);
    const pastedText = readPastedText();
    state.inputIntent = inputIntent;
    state.pastedText = pastedText;
    syncInputIntentControls();
    setRunStatus(`소스 ${state.runSourceFiles.length}개를 ${inputIntentLabel(inputIntent)} 모드로 파싱하는 중입니다...`, "loading");

    const queue = [...state.runSourceFiles];
    const containsPhoto = queue.some((file) => !isPdfFile(file));
    const aiFallback = aiFallbackForInputIntent(inputIntent, readAiFallbackForm());
    const filesPayload = await Promise.all(
      queue.map(async (file) => ({
        fileName: file.name,
        fileDataBase64: await fileToBase64(file),
      })),
    );

    const payload = {
      files: filesPayload,
      outputDir: document.getElementById("outputDirInput").value.trim(),
      exportMode,
      inputIntent: inputIntentConfig.payload,
      input_intent: inputIntentConfig.payloadSnake,
      sourceMode: inputIntentConfig.payload,
      source_mode: inputIntentConfig.payloadSnake,
      subject: document.getElementById("runSubjectSelect").value,
      ocr: document.getElementById("runOcrSelect").value,
      exportEdb: document.getElementById("runExportEdb").checked,
      detectPerspective: containsPhoto,
      maxDimension: containsPhoto ? 2400 : null,
      inputNotes: pastedText,
      input_notes: pastedText,
      pastedText,
      pasted_text: pastedText,
      aiFallback: {
        enabled: aiFallback.enabled,
        mode: aiFallback.mode,
        provider: aiFallback.provider,
        model: aiFallback.model,
        threshold: aiFallback.threshold,
        maxRegions: aiFallback.maxRegions,
        timeoutMs: aiFallback.timeoutMs,
        saveDebug: aiFallback.saveDebug,
      },
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
      throw new Error(result.error || `파싱 실행 실패 (${response.status})`);
    }

    state.previewMode = "problem";
    const normalizedSession = normalizeSession(result.session, "파싱 세션");
    const route = postParseRoute(normalizedSession, inputIntent);
    const nextScreenLabel = route.includes("view=review") ? "검수 화면" : "칠판 편집기";
    applySession(normalizedSession);
    clearQueuedFiles();
    writePastedText("");
    setRunStatus(
      `파싱 완료: ${exportModeLabel(normalizedSession.exportMode)} · ${normalizedSession.sourcePageCount || 0}페이지 · ${normalizedSession.detectedProblemCount || normalizedSession.problems.length}문항 · ${nextScreenLabel}로 이동합니다...`,
      "success",
    );
    setTimeout(() => { window.location.href = route; }, 900);
  } catch (error) {
    setRunStatus(`파싱 실패: ${error.message}`, "error");
    window.alert(`파싱 실패: ${error.message}`);
  } finally {
    state.runBusy = false;
    updateRuntimeControls();
  }
}

function renderFilmstrip(placements) {
  const root = document.getElementById("filmstripList");
  root.innerHTML = "";

  if (!placements.length) {
    root.innerHTML = `
      <div class="source-queue-empty">
        <p class="helper-text">현재 포함된 문항이 없습니다. 구성 편집을 눌러 문항을 복원하거나 새로 파싱해주세요.</p>
      </div>
    `;
    return;
  }

  placements.forEach((item, index) => {
    const entry = document.createElement("article");
    entry.className = `film-item${item.id === state.selectedId ? " is-selected" : ""}`;
    entry.draggable = true;
    entry.dataset.problemId = item.id;
    const problemLabel = getProblemLabel(item, index);
    const problemTitle = (item.title || "").trim() || problemLabel;
    entry.innerHTML = `
      <button class="film-item-delete" type="button" aria-label="${problemLabel} 삭제">&times;</button>
      <button class="film-item-main" type="button">
        <img class="film-thumb" src="${item.imagePath}" alt="${problemTitle}" draggable="false">
        <div class="film-copy">
          <div class="film-index">${problemLabel} · ${subjectLabel(item.subject)}</div>
          <div class="film-title">${problemTitle}</div>
          <div class="film-meta">
            <span>시작 ${item.startYPages.toFixed(1)}p</span>
            <span>높이 ${item.actualHeightPages.toFixed(2)}p</span>
            <span>${item.overflowAllowed ? "오버플로 허용" : "맞춤 우선"}</span>
          </div>
        </div>
      </button>
    `;

    entry.querySelector(".film-item-main")?.addEventListener("click", () => {
      state.selectedId = item.id;
      render();
    });
    entry.querySelector(".film-item-delete")?.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteProblem(item.id);
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
          <p class="helper-text">이 항목에 연결된 미리보기 이미지를 아직 찾지 못했습니다.</p>
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
    ? `원본 | ${subjectLabel(selected.subject)} | ${selected.sourceFileName || selected.sourcePageId || "페이지 정보 없음"}`
    : `문항 크롭 | ${subjectLabel(selected.subject)} | 시작 ${selected.startYPages.toFixed(1)}p`;
  root.innerHTML = renderImagePreview(getProblemHeading(selected), subtitle, imagePath);
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
        <span>필기 공간</span>
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
    const cardTitle = getProblemHeading(item);
    card.innerHTML = `
      <div class="board-card-header">
        <strong>${cardTitle}</strong>
        <span>${item.subject}</span>
      </div>
      <img src="${item.imagePath}" alt="${cardTitle}">
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
      snapLine.innerHTML = `<span class="snap-label">다음 시작 ${item.snappedNextStartYPages.toFixed(1)}p</span>`;
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
        렌더된 보드 이미지 열기
      </a>
    `;
    root.appendChild(renderLink);
  }
}

function renderInspector(selected) {
  const root = document.getElementById("inspectorContent");
  if (!selected) {
    root.innerHTML = `
      <div class="inspector-card">
        <h3>구성 비어 있음</h3>
        <p class="helper-text">현재 포함된 문항이 없습니다. 구성 편집 팝업에서 제외를 풀거나 새 파싱을 실행해주세요.</p>
      </div>
    `;
    return;
  }
  const session = state.session;
  const aiSummary = session.aiSummary || normalizeAiSummary(null);
  const aiFallback = session.aiFallback || normalizeAiFallbackConfig(null, null);
  const warnings = [...(session.warningMessages || [])];
  if (selected.overflowViolation) {
    warnings.push("이 문항은 1.2p보다 높지만 현재 오버플로가 꺼져 있습니다.");
  }
  if (selected.readingHeavy) {
    warnings.push("지문형 모드는 가독성을 우선해서 다음 문항 시작 지점을 더 뒤로 미룹니다.");
  }
  if (selected.snappedNextStartYPages - selected.startYPages > 2.4) {
    warnings.push("이 항목은 뒤 문항들을 크게 밀어내므로 다른 세트로 분리하는 편이 좋습니다.");
  }

  root.innerHTML = `
    <div class="inspector-card inspector-card-strong">
      <h3>${getProblemHeading(selected)}</h3>
      <p class="helper-text">${sessionSourceLabel(session.dataSource)} · ${exportModeLabel(session.exportMode)} · 입력 ${session.inputFileCount || 1}개</p>
      <div class="inspector-row">
        <label>과목</label>
        <span>${subjectLabel(selected.subject)}</span>
      </div>
      <div class="inspector-row">
        <label>원본 페이지 수</label>
        <span>${session.sourcePageCount || session.renderedPageFileUris.length || 0}개</span>
      </div>
      <div class="inspector-row">
        <label>감지 문항 수</label>
        <span>${session.detectedProblemCount || state.problems.length}개</span>
      </div>
      <div class="inspector-row">
        <label>시작</label>
        <span>${selected.startYPages.toFixed(1)}p</span>
      </div>
      <div class="inspector-row">
        <label>다음</label>
        <span>${selected.snappedNextStartYPages.toFixed(1)}p</span>
      </div>
      <div class="inspector-links">
        ${session.edbFileUri ? `<a class="text-link" href="${session.edbFileUri}" target="_blank" rel="noreferrer">EDB 열기</a>` : ""}
        ${selected.boardRenderPath ? `<a class="text-link" href="${selected.boardRenderPath}" target="_blank" rel="noreferrer">보드 렌더 열기</a>` : ""}
        ${selected.sourceImagePath ? `<a class="text-link" href="${selected.sourceImagePath}" target="_blank" rel="noreferrer">원본 이미지 열기</a>` : ""}
      </div>
    </div>

    <div class="inspector-card">
      <h3>빠른 조정</h3>
      <div class="inspector-row">
        <label for="heightRange">실제 콘텐츠 높이</label>
        <span id="heightOutput" class="range-output">${selected.actualHeightPages.toFixed(2)}p</span>
      </div>
      <input id="heightRange" type="range" min="0.6" max="2.4" step="0.02" value="${selected.actualHeightPages}">
      <div class="inspector-row">
        <label for="overflowToggle">오버플로 허용</label>
        <input id="overflowToggle" type="checkbox" ${selected.overflowAllowed ? "checked" : ""}>
      </div>
      <div class="inspector-row">
        <label for="readingToggle">지문형 모드</label>
        <input id="readingToggle" type="checkbox" ${selected.readingHeavy ? "checked" : ""}>
      </div>
      <div class="inspector-row">
        <label>레코드 구성</label>
        <span>${selected.recordMode || session.recordMode || "mixed"} · text ${selected.textRecordCount || 0} / image ${selected.imageRecordCount || 0}</span>
      </div>
      <div class="quick-actions">
        <button class="chip-button" id="moveUpButton" type="button">위로</button>
        <button class="chip-button" id="moveDownButton" type="button">아래로</button>
        <button class="chip-button" id="duplicateButton" type="button">복제</button>
        <button class="chip-button is-danger" id="deleteButton" type="button">삭제</button>
      </div>
    </div>

    ${(aiFallback.provided || aiSummary.requested || aiSummary.attemptedPageCount > 0 || aiSummary.appliedPageCount > 0) ? `
    <div class="inspector-card">
      <h3>AI 문항 보정</h3>
      <div class="inspector-row">
        <label>설정</label>
        <span>${aiModeLabel(aiFallback.mode)} · ${aiProviderLabel(aiFallback.provider)} · ${aiFallback.model}</span>
      </div>
      <div class="inspector-row">
        <label>페이지</label>
        <span>시도 ${aiSummary.attemptedPageCount}p / 적용 ${aiSummary.appliedPageCount}p</span>
      </div>
      <div class="inspector-row">
        <label>상태</label>
        <span>${formatAiStatusCounts(aiSummary.statusCounts) || "기록 없음"}</span>
      </div>
      ${aiFallback.provider === "gemini" ? `
      <p class="helper-text">Gemini API 키가 설정되어 있으면 애매한 문항 경계를 선택적으로 보정합니다.</p>
      ` : ""}
    </div>
    ` : ""}

    ${warnings.length ? `
    <div class="inspector-card">
      <h3>주의 사항</h3>
      <div class="warning-list">
        ${warnings.map((item) => `<div class="warning-item">${item}</div>`).join("")}
      </div>
    </div>
    ` : ""}
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

function renderComposerModal() {
  const root = document.getElementById("composerModal");
  if (!root) {
    return;
  }

  if (!state.composerOpen) {
    root.hidden = true;
    root.setAttribute("aria-hidden", "true");
    root.innerHTML = "";
    return;
  }

  const includedProblems = activeProblems();
  const excludedCount = state.problems.length - includedProblems.length;
  root.hidden = false;
  root.setAttribute("aria-hidden", "false");
  root.innerHTML = `
    <div class="modal-backdrop" data-close-composer="true"></div>
    <section class="modal-dialog" role="dialog" aria-modal="true" aria-labelledby="composerTitle">
      <div class="modal-header">
        <div>
          <p class="eyebrow">EDB 구성 편집</p>
          <h2 id="composerTitle">문항 순서와 포함 여부를 조정합니다</h2>
          <p class="subtle">순서 변경, 제목 수정, 제외/복원, 삭제를 팝업에서 바로 처리할 수 있습니다.</p>
        </div>
        <button class="modal-close-button" id="closeComposerButton" type="button" aria-label="닫기">×</button>
      </div>
      <div class="modal-summary-row">
        <span class="meta-pill">전체 ${state.problems.length}개</span>
        <span class="meta-pill">포함 ${includedProblems.length}개</span>
        <span class="meta-pill">제외 ${excludedCount}개</span>
      </div>
      <div class="composer-list">
        ${state.problems.map((problem, index) => `
          <article class="composer-item${problem.excluded ? " is-excluded" : ""}">
            ${problem.imagePath
              ? `<img class="composer-thumb" src="${problem.imagePath}" alt="${problem.title || `문항 ${index + 1}`}">`
              : `<div class="composer-thumb composer-thumb-placeholder">미리보기 없음</div>`}
            <div class="composer-main">
              <div class="composer-item-head">
                <strong>${getProblemHeading(problem, index)}</strong>
                <span class="meta-pill">${problem.excluded ? "제외됨" : "포함됨"}</span>
              </div>
              <div class="composer-field-grid">
                <label class="field-stack">
                  <span class="toolbar-label">제목</span>
                  <input class="composer-text-input" type="text" value="${(problem.title || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;")}" data-problem-title="${problem.id}">
                </label>
                <label class="field-stack">
                  <span class="toolbar-label">과목</span>
                  <select data-problem-subject="${problem.id}">
                    <option value="unknown"${problem.subject === "unknown" ? " selected" : ""}>과목 자동</option>
                    <option value="math"${problem.subject === "math" ? " selected" : ""}>수학</option>
                    <option value="science"${problem.subject === "science" ? " selected" : ""}>과학</option>
                    <option value="korean"${problem.subject === "korean" ? " selected" : ""}>국어</option>
                    <option value="english"${problem.subject === "english" ? " selected" : ""}>영어</option>
                    <option value="social"${problem.subject === "social" ? " selected" : ""}>사회</option>
                  </select>
                </label>
              </div>
              <div class="composer-actions">
                <button class="small-button" type="button" data-composer-move-up="${problem.id}">위로</button>
                <button class="small-button" type="button" data-composer-move-down="${problem.id}">아래로</button>
                <button class="ghost-button" type="button" data-composer-toggle="${problem.id}">${problem.excluded ? "복원" : "제외"}</button>
                <button class="ghost-button" type="button" data-composer-focus="${problem.id}">선택</button>
                <button class="ghost-button danger-ghost-button" type="button" data-composer-delete="${problem.id}">삭제</button>
              </div>
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;

  root.querySelectorAll("[data-close-composer]").forEach((node) => {
    node.addEventListener("click", closeComposerModal);
  });
  root.querySelector("#closeComposerButton")?.addEventListener("click", closeComposerModal);

  root.querySelectorAll("[data-problem-title]").forEach((input) => {
    input.addEventListener("change", (event) => {
      updateProblem(event.target.dataset.problemTitle, { title: event.target.value.trim() || "제목 없음" });
    });
  });

  root.querySelectorAll("[data-problem-subject]").forEach((select) => {
    select.addEventListener("change", (event) => {
      updateProblem(event.target.dataset.problemSubject, { subject: event.target.value });
    });
  });

  root.querySelectorAll("[data-composer-move-up]").forEach((button) => {
    button.addEventListener("click", () => moveProblem(button.dataset.composerMoveUp, -1));
  });
  root.querySelectorAll("[data-composer-move-down]").forEach((button) => {
    button.addEventListener("click", () => moveProblem(button.dataset.composerMoveDown, 1));
  });
  root.querySelectorAll("[data-composer-toggle]").forEach((button) => {
    button.addEventListener("click", () => toggleProblemExcluded(button.dataset.composerToggle));
  });
  root.querySelectorAll("[data-composer-focus]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedId = button.dataset.composerFocus;
      state.previewMode = "problem";
      render();
    });
  });
  root.querySelectorAll("[data-composer-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteProblem(button.dataset.composerDelete));
  });
}

function renderSummary(placements) {
  const maxBottom = placements.length ? Math.max(...placements.map((item) => item.actualBottomYPages)) : 0;
  document.getElementById("problemCount").textContent = String(placements.length);
  document.getElementById("pageCount").textContent = String(state.session.sourcePageCount || state.session.renderedPageFileUris.length || 0);
  document.getElementById("exportModeStat").textContent = exportModeLabel(state.session.exportMode);
  document.getElementById("boardUsage").textContent = `${maxBottom.toFixed(1)}p`;
}

function renderSessionSummary() {
  const node = document.getElementById("sessionSummaryText");
  const inputCount = state.session.inputFileCount || 0;
  const pageCount = state.session.sourcePageCount || state.session.renderedPageFileUris.length || 0;
  const problemCount = state.session.detectedProblemCount || state.problems.length;
  const includedCount = activeProblems().length;
  const excludedCount = state.problems.length - includedCount;
  const aiSummary = state.session.aiSummary || normalizeAiSummary(null);
  const aiFallback = state.session.aiFallback || normalizeAiFallbackConfig(null, null);
  let text = `${exportModeLabel(state.session.exportMode)} 변환 · 입력 ${inputCount}개 · 렌더 페이지 ${pageCount}개 · 감지 문항 ${problemCount}개 · 현재 포함 ${includedCount}개`;
  if (excludedCount > 0) {
    text += ` · 제외 ${excludedCount}개`;
  }
  if (aiSummary.requested || aiFallback.provided) {
    text += ` · AI ${aiModeLabel(aiFallback.mode)} ${aiProviderLabel(aiFallback.provider)}`;
    if (aiSummary.attemptedPageCount > 0 || aiSummary.appliedPageCount > 0) {
      text += ` · 시도 ${aiSummary.attemptedPageCount}p · 적용 ${aiSummary.appliedPageCount}p`;
    }
    const statusText = formatAiStatusCounts(aiSummary.statusCounts);
    if (statusText) {
      text += ` · ${statusText}`;
    }
  }
  if (state.session.warningMessages?.length) {
    text += ` · 주의: ${state.session.warningMessages[0]}`;
  }
  node.textContent = text;
}

function renderSessionHeader() {
  const sessionBadge = document.getElementById("sessionBadge");
  const edbStatus = document.getElementById("edbStatus");
  const fileCount = state.session.inputFileCount || 0;
  const pageCount = state.session.sourcePageCount || state.session.renderedPageFileUris.length || 0;
  sessionBadge.textContent = fileCount
    ? `${sessionSourceLabel(state.session.dataSource)} · 입력 ${fileCount}개 · ${pageCount}페이지`
    : sessionSourceLabel(state.session.dataSource);

  if (state.session.edbFileUri) {
    edbStatus.textContent = "EDB 열기";
    edbStatus.href = state.session.edbFileUri;
    edbStatus.classList.remove("is-disabled");
  } else {
    edbStatus.textContent = "EDB 없음";
    edbStatus.href = "#";
    edbStatus.classList.add("is-disabled");
  }
}

function render() {
  const placements = computePlacements();
  const selected = ensureSelectedProblem(placements);
  updateRuntimeControls();

  document.querySelectorAll("[data-preview-mode]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.previewMode === state.previewMode);
  });
  document.getElementById("selectedSubject").textContent = selected ? subjectLabel(selected.subject) : "없음";
  document.getElementById("selectedPlacement").textContent = selected
    ? `시작 ${selected.startYPages.toFixed(1)}p | 다음 ${selected.snappedNextStartYPages.toFixed(1)}p`
    : "포함된 문항 없음";
  renderSessionHeader();
  renderSessionSummary();
  renderSourceQueue();

  if (!selected) {
    document.getElementById("previewTitle").textContent = "구성 비어 있음";
    document.getElementById("previewSubtitle").textContent = "구성 편집 팝업에서 제외를 풀거나 새 파싱을 실행해주세요.";
    document.getElementById("previewSurface").innerHTML = `
      <div class="preview-card">
        <div class="preview-image-frame preview-empty">
          <p class="helper-text">현재 포함된 문항이 없습니다.</p>
        </div>
      </div>
    `;
  } else if (state.previewMode === "source") {
    document.getElementById("previewTitle").textContent = "원본 미리보기";
    document.getElementById("previewSubtitle").textContent = "현재 문항의 원본 페이지나 촬영 이미지를 확인합니다.";
    renderSourceOrProblemPreview(selected, "source");
  } else if (state.previewMode === "problem") {
    document.getElementById("previewTitle").textContent = "문항 미리보기";
    document.getElementById("previewSubtitle").textContent = "자동 파싱으로 잘린 문항 자산을 바로 검수합니다.";
    renderSourceOrProblemPreview(selected, "problem");
  } else {
    document.getElementById("previewTitle").textContent = "보드 미리보기";
    document.getElementById("previewSubtitle").textContent = "문항 배치와 렌더된 보드 이미지를 함께 확인합니다.";
    renderBoardPreview(placements, selected);
  }

  renderFilmstrip(placements);
  renderInspector(selected);
  renderSummary(placements);
  renderComposerModal();
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

document.getElementById("runLayoutModeSelect").addEventListener("change", () => {
  const layoutModeSelect = document.getElementById("runLayoutModeSelect");
  if (layoutModeSelect?.value === "page") {
    state.inputIntent = "page-as-is";
  } else if (state.inputIntent === "page-as-is") {
    state.inputIntent = DEFAULT_INPUT_INTENT;
  }
  syncInputIntentControls();
  updateRuntimeControls();
});

document.querySelectorAll('input[name="inputIntent"]').forEach((input) => {
  input.addEventListener("change", (event) => {
    state.inputIntent = normalizeInputIntent(event.target.value);
    syncInputIntentControls();
    if (state.inputIntent === "multi-problem") {
      writeAiFallbackForm(aiFallbackForInputIntent(state.inputIntent, readAiFallbackForm()));
      syncAiFallbackControls();
    }
    updateRuntimeControls();
    setRunStatus(`${inputIntentLabel(state.inputIntent)} 입력 형태로 설정했습니다.`, "neutral");
  });
});

[
  "runAiFallbackEnabled",
  "runAiFallbackModeSelect",
  "runAiProviderSelect",
  "runAiModelInput",
  "runAiThresholdInput",
  "runAiMaxRegionsInput",
  "runAiTimeoutInput",
  "runAiSaveDebug",
].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", syncAiFallbackControls);
});
document.getElementById("runAiModelInput")?.addEventListener("input", syncAiFallbackControls);
document.getElementById("applyGeminiPresetButton")?.addEventListener("click", () => applyAiPreset("gemini"));

document.getElementById("saveGeminiKeyButton")?.addEventListener("click", () => {
  const input = document.getElementById("geminiApiKeyInput");
  saveGeminiApiKey(input?.value || "");
});
document.getElementById("clearGeminiKeyButton")?.addEventListener("click", () => {
  if (!window.confirm("저장된 Gemini API 키를 삭제할까요?")) {
    return;
  }
  saveGeminiApiKey("");
});
document.getElementById("toggleGeminiKeyVisibility")?.addEventListener("click", (event) => {
  const button = event.currentTarget;
  const input = document.getElementById("geminiApiKeyInput");
  if (!input) {
    return;
  }
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  button.setAttribute("aria-pressed", showing ? "false" : "true");
  button.textContent = showing ? "보기" : "숨기기";
});
document.getElementById("geminiApiKeyInput")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    saveGeminiApiKey(event.target.value || "");
  }
});

document.getElementById("openComposerButton").addEventListener("click", openComposerModal);

const addReadingHeavyButton = document.getElementById("addReadingHeavy");
if (addReadingHeavyButton) {
  addReadingHeavyButton.addEventListener("click", addLongPassage);
}

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
    window.alert(`세션 JSON을 불러오지 못했습니다: ${error.message}`);
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
    setRunStatus("로컬 앱 서버에서 최근 세션을 불러왔습니다.", "success");
  } catch (error) {
    setRunStatus(`최근 세션 불러오기 실패: ${error.message}`, "error");
  }
});

document.getElementById("useSampleButton").addEventListener("click", () => {
  applySession(sampleSession);
  setRunStatus("번들된 샘플 데이터로 전환했습니다.", "neutral");
});

document.getElementById("clearAllButton").addEventListener("click", clearAllState);

const sourceFileInput = document.getElementById("sourceFileInput");
const cameraFileInput = document.getElementById("cameraFileInput");
const sourceDropzone = document.getElementById("sourceDropzone");

function isEditablePasteTarget(target) {
  const tagName = String(target?.tagName || "").toUpperCase();
  return tagName === "INPUT" || tagName === "TEXTAREA" || Boolean(target?.isContentEditable);
}

function collectClipboardSourceFiles(clipboardData) {
  if (!clipboardData) {
    return [];
  }

  const itemFiles = Array.from(clipboardData.items || [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter(Boolean);
  const rawFiles = itemFiles.length ? itemFiles : Array.from(clipboardData.files || []);
  return rawFiles
    .map((file, index) => normalizeClipboardFile(file, index))
    .filter(isSupportedSourceFile);
}

async function handleDocumentPaste(event) {
  const clipboardData = event.clipboardData;
  const files = collectClipboardSourceFiles(clipboardData);
  if (files.length) {
    event.preventDefault();
    const addedCount = updateQueuedFiles(files, { replace: false });
    if (addedCount) {
      setRunStatus(`${addedCount}개 클립보드 파일을 업로드 큐에 추가했습니다.`, "neutral");
      await maybeAutoRun();
    }
    return;
  }

  const text = clipboardData?.getData("text/plain") || "";
  if (!text.trim() || isEditablePasteTarget(event.target)) {
    return;
  }

  event.preventDefault();
  writePastedText(text.trim());
  setRunStatus("클립보드 텍스트를 붙여넣었습니다. 사진/PDF와 함께 참고 메모로 전송됩니다.", "neutral");
}

document.getElementById("openCameraButton").addEventListener("click", () => {
  cameraFileInput.click();
});

document.getElementById("chooseSourceButton").addEventListener("click", () => {
  sourceFileInput.click();
});

const clearSourceButton = document.getElementById("clearSourceButton");
if (clearSourceButton) {
  clearSourceButton.addEventListener("click", () => {
    clearQueuedFiles();
    sourceFileInput.value = "";
    cameraFileInput.value = "";
  });
}

document.getElementById("autoParseToggle").addEventListener("change", (event) => {
  state.autoParse = event.target.checked;
  updateRuntimeControls();
});

document.getElementById("pastedTextInput")?.addEventListener("input", (event) => {
  state.pastedText = event.target.value.trim();
  updateRuntimeControls();
});

document.getElementById("focusPasteTextButton")?.addEventListener("click", () => {
  const input = document.getElementById("pastedTextInput");
  input?.focus();
  input?.select();
});

document.getElementById("clearPastedTextButton")?.addEventListener("click", clearPastedText);
document.addEventListener("paste", (event) => {
  handleDocumentPaste(event);
});

sourceFileInput.addEventListener("change", async (event) => {
  const addedCount = updateQueuedFiles(event.target.files, { replace: false });
  event.target.value = "";
  if (addedCount) {
    setRunStatus(`${addedCount}개 파일을 업로드 큐에 추가했습니다.`, "neutral");
  }
  await maybeAutoRun();
});

cameraFileInput.addEventListener("change", async (event) => {
  const addedCount = updateQueuedFiles(event.target.files, { replace: false });
  event.target.value = "";
  if (addedCount) {
    setRunStatus(`${addedCount}개 카메라 파일을 업로드 큐에 추가했습니다.`, "neutral");
  }
  await maybeAutoRun();
});

sourceDropzone.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  event.preventDefault();
  sourceFileInput.click();
});
sourceDropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  sourceDropzone.classList.add("is-drag-over");
});
sourceDropzone.addEventListener("dragleave", () => {
  sourceDropzone.classList.remove("is-drag-over");
});
sourceDropzone.addEventListener("drop", async (event) => {
  event.preventDefault();
  sourceDropzone.classList.remove("is-drag-over");
  const addedCount = updateQueuedFiles(event.dataTransfer?.files, { replace: false });
  if (addedCount) {
    setRunStatus(`${addedCount}개 파일을 업로드 큐에 추가했습니다.`, "neutral");
  }
  await maybeAutoRun();
});

document.getElementById("runExportButton").addEventListener("click", runExportFromApi);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.composerOpen) {
    closeComposerModal();
  }
});

async function initializeRuntimeConnection() {
  writeAiFallbackForm(state.session.aiFallback);
  syncAiFallbackControls();
  syncInputIntentControls();
  writePastedText(state.pastedText);
  syncTemplateSelect();
  render();
  await probeApi();
  if (!state.apiAvailable) {
    const helper = document.getElementById("geminiKeyHelper");
    if (helper) {
      helper.textContent = "로컬 앱 서버가 꺼져 있어 사용자 설정을 저장할 수 없습니다.";
      helper.classList.add("is-warning");
    }
    return;
  }
  await fetchUserSettings();
  try {
    const latestSession = await fetchLatestSessionFromApi();
    applySession(latestSession);
    setRunStatus("로컬 앱 서버에서 최근 세션을 불러왔습니다.", "success");
  } catch (error) {
    setRunStatus("로컬 앱 서버가 연결되었습니다. 사진이나 PDF를 넣어 첫 파싱을 시작하세요.", "neutral");
  }
}

initializeRuntimeConnection();
