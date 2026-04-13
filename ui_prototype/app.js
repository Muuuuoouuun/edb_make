const BASE_SLOT_HEIGHT = 1.2;

const DEFAULT_OUTPUT_DIR = "mvp_export_app";
const DEFAULT_AI_FALLBACK = Object.freeze({
  enabled: false,
  mode: "off",
  provider: "openai",
  model: "gpt-4o-mini",
  apiKey: "",
  interventionLevel: 0,
  threshold: 0.72,
  maxRegions: 18,
  timeoutMs: 12000,
  saveDebug: false,
  failOnError: false,
  provided: false,
});
const DEFAULT_AI_CAPABILITIES = Object.freeze({
  available: false,
  supportedModes: ["off", "auto", "force"],
  defaultProvider: "openai",
  missingApiKeys: [],
  readyProviders: [],
  providers: {
    openai: {
      supported: true,
      supportedModes: ["off", "auto", "force"],
      apiKeyEnv: "OPENAI_API_KEY",
      apiKeyEnvs: ["OPENAI_API_KEY"],
      apiKeyPresent: false,
      available: false,
      status: "missing_api_key",
      supportsVision: true,
    },
    gemini: {
      supported: true,
      supportedModes: ["off", "auto", "force"],
      apiKeyEnv: "GEMINI_API_KEY or GOOGLE_API_KEY",
      apiKeyEnvs: ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
      apiKeyPresent: false,
      available: false,
      status: "missing_api_key",
      supportsVision: true,
    },
  },
  provided: false,
});
const AI_PROVIDER_PRESETS = Object.freeze({
  openai: {
    provider: "openai",
    model: "gpt-4o-mini",
    interventionLevel: 0,
    threshold: 0.72,
    maxRegions: 18,
    timeoutMs: 12000,
  },
  gemini: {
    provider: "gemini",
    model: "gemini-2.5-flash",
    interventionLevel: 0,
    threshold: 0.72,
    maxRegions: 18,
    timeoutMs: 15000,
  },
});

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
    fixedLeftRatio: 0.5,
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

const RUN_PRESETS = Object.freeze({
  "quick-check": {
    label: "빠른 확인",
    badge: "빠른 확인 프리셋",
    description: "OCR 없이 문항 분리와 크롭만 먼저 확인",
    exportMode: "question",
    subject: "unknown",
    ocr: "none",
    exportEdb: false,
    ai: {
      enabled: false,
      mode: "off",
      interventionLevel: 0,
    },
  },
  "default-parse": {
    label: "기본 파싱",
    badge: "기본 파싱 프리셋",
    description: "문항별 자동 파싱의 기본값",
    exportMode: "question",
    subject: "unknown",
    ocr: "auto",
    exportEdb: true,
    ai: {
      enabled: false,
      mode: "off",
      interventionLevel: 0,
    },
  },
  "ai-structure": {
    label: "AI 구조 보정",
    badge: "AI 구조 보정 프리셋",
    description: "애매한 페이지는 AI가 구조와 배치를 먼저 보정",
    exportMode: "question",
    subject: "unknown",
    ocr: "auto",
    exportEdb: true,
    ai: {
      enabled: true,
      mode: "auto",
      interventionLevel: 0,
    },
  },
  "ai-rebuild": {
    label: "AI 재구성",
    badge: "AI 재구성 프리셋",
    description: "최종 출력 품질과 업스케일을 우선",
    exportMode: "question",
    subject: "unknown",
    ocr: "auto",
    exportEdb: true,
    ai: {
      enabled: true,
      mode: "auto",
      interventionLevel: 2,
    },
  },
});

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
    name: template.name || "생성 세션",
    baseSlotHeight: toNumber(template.base_slot_height_pages ?? template.baseSlotHeight, BASE_SLOT_HEIGHT),
    boardPageCount: toNumber(template.board_page_count ?? template.boardPageCount, 50),
    fixedLeftRatio: toNumber(template.fixed_left_zone_ratio ?? template.fixedLeftRatio, 0.5),
  };
}

function normalizeAiFallbackConfig(rawConfig, rawSummary) {
  const config = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  const summary = rawSummary && typeof rawSummary === "object" ? rawSummary : {};
  const rawMode = String(config.mode ?? summary.mode ?? DEFAULT_AI_FALLBACK.mode).trim().toLowerCase();
  const mode = ["auto", "force", "off"].includes(rawMode) ? rawMode : DEFAULT_AI_FALLBACK.mode;
  const rawProvider = String(config.provider ?? summary.provider ?? DEFAULT_AI_FALLBACK.provider).trim().toLowerCase();
  const provider = rawProvider === "gemini" ? "gemini" : "openai";
  const defaultPreset = AI_PROVIDER_PRESETS[provider] || AI_PROVIDER_PRESETS.openai;
  const model = String(config.model ?? summary.model ?? defaultPreset.model).trim() || defaultPreset.model;
  const interventionLevel = Math.min(
    2,
    Math.max(
      0,
      Math.round(toNumber(config.intervention_level ?? config.interventionLevel ?? summary.intervention_level ?? summary.interventionLevel, DEFAULT_AI_FALLBACK.interventionLevel)),
    ),
  );
  const threshold = Math.min(1, Math.max(0, toNumber(config.threshold ?? config.aiThreshold ?? summary.threshold, defaultPreset.threshold)));
  const maxRegions = Math.max(1, Math.round(toNumber(config.max_regions ?? config.maxRegions ?? summary.max_regions ?? summary.maxRegions, defaultPreset.maxRegions)));
  const timeoutMs = Math.max(1000, Math.round(toNumber(config.timeout_ms ?? config.timeoutMs ?? summary.timeout_ms ?? summary.timeoutMs, defaultPreset.timeoutMs)));
  const enabled = Boolean(config.enabled ?? summary.requested ?? (mode !== "off"));

  return {
    enabled,
    mode,
    provider,
    model,
    apiKey: String(config.api_key ?? config.apiKey ?? "").trim(),
    interventionLevel,
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
      interventionLevel: DEFAULT_AI_FALLBACK.interventionLevel,
      attemptedPageCount: 0,
      appliedPageCount: 0,
      recommendedPageCount: 0,
      localRetryRecommendedPageCount: 0,
      routeCounts: {},
      routeTierCounts: {},
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
    interventionLevel: Math.min(
      2,
      Math.max(
        0,
        Math.round(toNumber(rawSummary.intervention_level ?? rawSummary.interventionLevel, DEFAULT_AI_FALLBACK.interventionLevel)),
      ),
    ),
    attemptedPageCount: Math.max(0, Math.round(toNumber(rawSummary.attempted_page_count ?? rawSummary.attemptedPageCount, 0))),
    appliedPageCount: Math.max(0, Math.round(toNumber(rawSummary.applied_page_count ?? rawSummary.appliedPageCount, 0))),
    recommendedPageCount: Math.max(
      0,
      Math.round(
        toNumber(
          rawSummary.recommended_page_count
            ?? rawSummary.recommendedPageCount
            ?? rawStatusCounts.ai_recommended,
          0,
        ),
      ),
    ),
    localRetryRecommendedPageCount: Math.max(
      0,
      Math.round(
        toNumber(
          rawSummary.local_retry_recommended_page_count
            ?? rawSummary.localRetryRecommendedPageCount
            ?? rawStatusCounts.local_retry_recommended,
          0,
        ),
      ),
    ),
    routeCounts: rawSummary.route_counts || rawSummary.routeCounts || {},
    routeTierCounts: rawSummary.route_tier_counts || rawSummary.routeTierCounts || {},
    statusCounts,
    provided: true,
  };
}

function normalizeAiCapabilities(rawCapabilities) {
  if (!rawCapabilities || typeof rawCapabilities !== "object") {
    return {
      ...DEFAULT_AI_CAPABILITIES,
      providers: { ...DEFAULT_AI_CAPABILITIES.providers },
      provided: false,
    };
  }

  const rawProviders = rawCapabilities.providers && typeof rawCapabilities.providers === "object"
    ? rawCapabilities.providers
    : {};
  const providers = Object.entries(rawProviders).reduce((accumulator, [providerName, providerInfo]) => {
    const info = providerInfo && typeof providerInfo === "object" ? providerInfo : {};
    accumulator[String(providerName)] = {
      supported: Boolean(info.supported),
      supportedModes: Array.isArray(info.supported_modes || info.supportedModes)
        ? (info.supported_modes || info.supportedModes).map(String)
        : [],
      apiKeyEnv: String(info.api_key_env || info.apiKeyEnv || ""),
      apiKeyEnvs: Array.isArray(info.api_key_envs || info.apiKeyEnvs)
        ? (info.api_key_envs || info.apiKeyEnvs).map(String)
        : [],
      apiKeyPresent: Boolean(info.api_key_present ?? info.apiKeyPresent),
      available: Boolean(info.available),
      status: String(info.status || "unknown"),
      supportsVision: Boolean(info.supports_vision ?? info.supportsVision),
    };
    return accumulator;
  }, {});

  return {
    available: Boolean(rawCapabilities.available),
    supportedModes: Array.isArray(rawCapabilities.supported_modes || rawCapabilities.supportedModes)
      ? (rawCapabilities.supported_modes || rawCapabilities.supportedModes).map(String)
      : [...DEFAULT_AI_CAPABILITIES.supportedModes],
    defaultProvider: String(rawCapabilities.default_provider || rawCapabilities.defaultProvider || DEFAULT_AI_CAPABILITIES.defaultProvider),
    missingApiKeys: Array.isArray(rawCapabilities.missing_api_keys || rawCapabilities.missingApiKeys)
      ? (rawCapabilities.missing_api_keys || rawCapabilities.missingApiKeys).map(String)
      : [],
    readyProviders: Array.isArray(rawCapabilities.ready_providers || rawCapabilities.readyProviders)
      ? (rawCapabilities.ready_providers || rawCapabilities.readyProviders).map(String)
      : [],
    providers: {
      ...DEFAULT_AI_CAPABILITIES.providers,
      ...providers,
    },
    provided: true,
  };
}

function formatApiKeyEnvLabel(providerInfo) {
  if (!providerInfo) {
    return "API 키";
  }
  if (Array.isArray(providerInfo.apiKeyEnvs) && providerInfo.apiKeyEnvs.length) {
    return providerInfo.apiKeyEnvs.join(" or ");
  }
  return providerInfo.apiKeyEnv || "API 키";
}

function hasLiveProviderKey(liveConfig, providerInfo) {
  return Boolean((liveConfig.apiKey || "").trim() || providerInfo?.apiKeyPresent);
}

function formatAiRecommendationSummary(aiSummary) {
  const items = [];
  if (aiSummary.recommendedPageCount > 0) {
    items.push(`AI 권장 ${aiSummary.recommendedPageCount}p`);
  }
  if (aiSummary.localRetryRecommendedPageCount > 0) {
    items.push(`로컬 재시도 권장 ${aiSummary.localRetryRecommendedPageCount}p`);
  }
  return items.join(" · ");
}

function getAiQuickElements() {
  return {
    modePill: document.getElementById("aiQuickModePill"),
    providerPill: document.getElementById("aiQuickProviderPill"),
    keyPill: document.getElementById("aiQuickKeyPill"),
    resultPill: document.getElementById("aiQuickResultPill"),
    advancedHintPill: document.getElementById("aiAdvancedHintPill"),
    quickStatusText: document.getElementById("aiFallbackHelper"),
    advancedDetails: document.getElementById("advancedSettingsPanel"),
    openAdvancedButton: document.getElementById("openAiAdvancedButton"),
  };
}

function getAiUiSnapshot() {
  const liveConfig = readAiFallbackForm();
  const sessionSummary = state.session.aiSummary || normalizeAiSummary(null);
  const sessionFallback = state.session.aiFallback || normalizeAiFallbackConfig(null, null);
  const aiCapabilities = state.aiCapabilities || state.session.aiCapabilities || normalizeAiCapabilities(null);
  return { liveConfig, sessionSummary, sessionFallback, aiCapabilities };
}

function aiToneClass(tone) {
  if (tone === "warning") {
    return "is-warning";
  }
  if (tone === "success") {
    return "is-success";
  }
  if (tone === "danger") {
    return "is-danger";
  }
  return "";
}

function resolveAiQuickState() {
  const { liveConfig, sessionSummary, aiCapabilities } = getAiUiSnapshot();
  const providerLabel = aiProviderLabel(liveConfig.provider);
  const modeLabel = aiModeLabel(liveConfig.mode);
  const interventionLabel = aiInterventionLabel(liveConfig.interventionLevel);
  const recommendationText = formatAiRecommendationSummary(sessionSummary);
  const statusCounts = sessionSummary.statusCounts || {};
  const providerInfo = aiCapabilities.providers?.[liveConfig.provider] || normalizeAiCapabilities(null).providers.openai;
  const apiKeyLabel = formatApiKeyEnvLabel(providerInfo);
  const localApiKeyPresent = Boolean((liveConfig.apiKey || "").trim());
  const providerHasKey = hasLiveProviderKey(liveConfig, providerInfo);

  let keyLabel = "키 상태 미확인";
  let keyTone = "neutral";
  let statusLine = "AI 보정은 메인 패널에서 바로 켤 수 있습니다.";

  if (!state.apiAvailable) {
    keyLabel = "서버 연결 필요";
    keyTone = "warning";
    statusLine = "로컬 앱 서버에 연결되면 현재 AI 가능 여부와 키 상태를 바로 확인합니다.";
  } else if (!providerInfo.supported) {
    keyLabel = "공급자 미지원";
    keyTone = "warning";
    statusLine = `${providerLabel}는 현재 이 앱 빌드에서 지원하지 않습니다.`;
  } else if (!providerHasKey) {
    keyLabel = "API 키 없음";
    keyTone = "warning";
    statusLine = `${apiKeyLabel}를 환경변수, .env/.app_runtime/ai.env, 또는 위 입력칸으로 제공해야 ${providerLabel} AI 보정을 실행할 수 있습니다.`;
  } else if (localApiKeyPresent) {
    keyLabel = "탭 키 사용";
    keyTone = "success";
  } else if (providerInfo.apiKeyPresent) {
    keyLabel = "로컬 키 연결";
    keyTone = "success";
  }

  if (state.apiAvailable && providerInfo.supported && providerHasKey) {
    if (statusCounts.applied > 0 || statusCounts.cache_hit > 0) {
      statusLine = "지난 실행에서 AI 보정이 적용되었습니다.";
    } else if (statusCounts.missing_api_key > 0) {
      statusLine = "지난 실행에서는 API 키가 없어 AI 보정이 건너뛰어졌지만, 지금은 다시 실행할 준비가 됐습니다.";
    } else if (statusCounts.provider_pending > 0) {
      statusLine = "지난 실행에서는 지원되지 않는 공급자로 건너뛴 기록이 있습니다.";
    } else if (liveConfig.enabled && liveConfig.mode === "force") {
      statusLine = `${interventionLabel} · ${providerLabel} ${liveConfig.model}로 가능한 후보를 모두 보정합니다.`;
    } else if (liveConfig.enabled && liveConfig.mode === "auto") {
      statusLine = `${interventionLabel} · ${providerLabel} ${liveConfig.model}로 권장 문항만 먼저 보정합니다.`;
    } else {
      statusLine = localApiKeyPresent
        ? "현재 탭에 입력한 키가 준비되어 있습니다. 필요할 때 바로 AI 보정을 켤 수 있습니다."
        : "AI 보정은 꺼져 있습니다. 필요할 때만 켜세요.";
    }
  } else if (!state.apiAvailable || !providerInfo.supported || !providerHasKey) {
    // The more specific status messages above already describe the blocking reason.
  } else {
    statusLine = "AI 보정은 꺼져 있습니다. 필요할 때만 켜세요.";
  }

  const resultLabel = recommendationText || "권장 없음";
  const resultTone = recommendationText ? "success" : "neutral";

  return {
    modeLabel,
    providerLabel,
    keyLabel,
    keyTone,
    resultLabel,
    resultTone,
    statusLine,
    recommendationText,
    liveConfig,
    sessionSummary,
    sessionFallback,
    aiCapabilities,
    interventionLabel,
  };
}

function renderAiQuickPanel() {
  const elements = getAiQuickElements();
  const snapshot = resolveAiQuickState();

  if (elements.modePill) {
    elements.modePill.textContent = snapshot.modeLabel;
    elements.modePill.className = `meta-pill ${aiToneClass(snapshot.liveConfig.enabled && snapshot.liveConfig.mode === "force" ? "danger" : snapshot.liveConfig.enabled ? "success" : "neutral")}`.trim();
  }
  if (elements.providerPill) {
    elements.providerPill.textContent = snapshot.providerLabel;
    elements.providerPill.className = "meta-pill";
  }
  if (elements.keyPill) {
    elements.keyPill.textContent = snapshot.keyLabel;
    elements.keyPill.className = `meta-pill ${aiToneClass(snapshot.keyTone)}`.trim();
  }
  if (elements.resultPill) {
    elements.resultPill.textContent = snapshot.resultLabel;
    elements.resultPill.className = `meta-pill ${aiToneClass(snapshot.resultTone)}`.trim();
  }
  if (elements.advancedHintPill) {
    elements.advancedHintPill.textContent = snapshot.liveConfig.enabled ? `${snapshot.interventionLabel} · ${snapshot.providerLabel}` : "세부 값 조정";
    elements.advancedHintPill.className = `meta-pill ${aiToneClass(snapshot.liveConfig.enabled ? "success" : "neutral")}`.trim();
  }
  if (elements.quickStatusText) {
    elements.quickStatusText.textContent = snapshot.statusLine;
    elements.quickStatusText.classList.remove("is-warning", "is-success", "is-danger");
    if (snapshot.keyTone !== "neutral") {
      elements.quickStatusText.classList.add(aiToneClass(snapshot.keyTone));
    }
  }
  if (elements.openAdvancedButton) {
    elements.openAdvancedButton.textContent = elements.advancedDetails?.open ? "세부 설정 접기" : "세부 설정 펼치기";
  }
}

function openAiAdvancedSettings() {
  const details = document.getElementById("advancedSettingsPanel");
  if (!details) {
    return;
  }
  details.open = true;
  renderAiQuickPanel();
  details.scrollIntoView({ behavior: "smooth", block: "start" });
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
    imagePath: normalizePath(
      problem.finalImagePath
        || problem.final_image_path
        || problem.presentationPath
        || problem.presentation_path
        || problem.imagePath
        || problem.image_path
        || problem.cutoutPath
        || problem.cutout_path
        || problem.cropPath
        || problem.crop_path
        || problem.sourceImagePath
        || "",
    ),
    cropPath: normalizePath(problem.cropPath || problem.crop_path || ""),
    sourceImagePath: normalizePath(
      problem.sourceImagePath
        || problem.source_image_path
        || problem.cropPath
        || problem.crop_path
        || problem.imagePath
        || "",
    ),
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
    aiInterventionLevel: Math.min(2, Math.max(0, Math.round(toNumber(problem.aiInterventionLevel ?? problem.ai_intervention_level, 0)))),
    aiInterventionLabel: String(problem.aiInterventionLabel || problem.ai_intervention_label || ""),
    renderScaleFactor: toNumber(problem.renderScaleFactor ?? problem.render_scale_factor, 1),
    excluded: Boolean(problem.excluded ?? problem.isExcluded),
  };
}

function normalizeSession(rawSession, fallbackName = "불러온 세션") {
  const rawProblems = Array.isArray(rawSession?.problems) ? rawSession.problems : [];
  const aiSummary = normalizeAiSummary(rawSession?.ai_summary || rawSession?.aiSummary);
  const aiFallback = normalizeAiFallbackConfig(rawSession?.ai_fallback || rawSession?.aiFallback, rawSession?.ai_summary || rawSession?.aiSummary);
  const aiCapabilities = normalizeAiCapabilities(rawSession?.ai_capabilities || rawSession?.aiCapabilities);
  return {
    sessionName: rawSession?.session_name || rawSession?.sessionName || fallbackName,
    dataSource: rawSession?.data_source || rawSession?.dataSource || "manual",
    generatedAt: rawSession?.generated_at || rawSession?.generatedAt || "",
    outputDir: rawSession?.output_dir || rawSession?.outputDir || "",
    sourceMode: rawSession?.source_mode || rawSession?.sourceMode || "single",
    exportMode: rawSession?.export_mode || rawSession?.exportMode || "question",
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
    renderedPageFileUris: (rawSession?.rendered_page_file_uris || rawSession?.renderedPageFileUris || rawSession?.rendered_page_paths || rawSession?.renderedPagePaths || []).map(normalizePath),
    template: normalizeTemplate(rawSession?.template),
    warningMessages: Array.isArray(rawSession?.warning_messages || rawSession?.warningMessages)
      ? (rawSession?.warning_messages || rawSession?.warningMessages)
      : [],
    parseDiagnostics: rawSession?.parse_diagnostics || rawSession?.parseDiagnostics || {},
    aiFallback,
    aiSummary,
    aiCapabilities,
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

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0KB";
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

function buildApiErrorMessage(payload, fallbackMessage) {
  const detail = payload?.error_detail || payload?.errorDetail || {};
  const primary = String(detail.message || payload?.error || fallbackMessage || "요청 처리에 실패했습니다.").trim();
  const hint = String(detail.hint || "").trim();
  if (!hint || primary.includes(hint)) {
    return primary;
  }
  return `${primary} ${hint}`;
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
    empty: "대기 중",
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

function aiProviderLabel(provider) {
  if (provider === "gemini") {
    return "Gemini";
  }
  if (provider === "claude" || provider === "anthropic") {
    return "Claude";
  }
  return "OpenAI";
}

function aiModeLabel(mode) {
  if (mode === "force") {
    return "강제 실행";
  }
  if (mode === "auto") {
    return "권장 시 실행";
  }
  return "권장만 표시";
}

function aiInterventionLabel(level) {
  if (level === 2) {
    return "2단계 재구성/업스케일";
  }
  if (level === 1) {
    return "1단계 파싱/색보정";
  }
  return "0단계 구조/크롭/배치";
}

function aiStatusLabel(status) {
  const labels = {
    applied: "적용됨",
    applied_with_warnings: "적용됨",
    ai_recommended: "AI 권장",
    cache_hit: "캐시 사용",
    disabled: "비활성",
    error: "요청 실패",
    invalid_response: "응답 오류",
    local_retry_recommended: "로컬 재시도 권장",
    missing_api_key: "키 없음",
    not_needed: "불필요",
    provider_pending: "공급자 미지원",
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
    interventionLevel: document.getElementById("runAiInterventionLevelSelect"),
    apiKey: document.getElementById("runAiApiKeyInput"),
    model: document.getElementById("runAiModelInput"),
    threshold: document.getElementById("runAiThresholdInput"),
    maxRegions: document.getElementById("runAiMaxRegionsInput"),
    timeoutMs: document.getElementById("runAiTimeoutInput"),
    saveDebug: document.getElementById("runAiSaveDebug"),
    openAiPresetButton: document.getElementById("applyOpenAiPresetButton"),
    geminiPresetButton: document.getElementById("applyGeminiPresetButton"),
    clearApiKeyButton: document.getElementById("clearAiApiKeyButton"),
    helper: document.getElementById("aiFallbackHelper"),
  };
}

function readAiFallbackForm() {
  const controls = getAiControlElements();
  const provider = controls.provider?.value === "gemini" ? "gemini" : "openai";
  const preset = AI_PROVIDER_PRESETS[provider] || AI_PROVIDER_PRESETS.openai;
  const requestedMode = String(controls.mode?.value || DEFAULT_AI_FALLBACK.mode).trim().toLowerCase();
  const mode = controls.enabled?.checked ? requestedMode : "off";

  return {
    enabled: mode !== "off",
    mode: ["auto", "force", "off"].includes(mode) ? mode : "off",
    provider,
    apiKey: (controls.apiKey?.value || "").trim(),
    model: (controls.model?.value || "").trim() || preset.model,
    interventionLevel: Math.min(2, Math.max(0, Math.round(toNumber(controls.interventionLevel?.value, DEFAULT_AI_FALLBACK.interventionLevel)))),
    threshold: Math.min(1, Math.max(0, toNumber(controls.threshold?.value, preset.threshold))),
    maxRegions: Math.max(1, Math.round(toNumber(controls.maxRegions?.value, preset.maxRegions))),
    timeoutMs: Math.max(1000, Math.round(toNumber(controls.timeoutMs?.value, preset.timeoutMs))),
    saveDebug: Boolean(controls.saveDebug?.checked),
  };
}

function writeAiFallbackForm(config, options = {}) {
  const controls = getAiControlElements();
  const normalized = normalizeAiFallbackConfig(config, null);
  const preserveApiKey = options.preserveApiKey !== false;
  const existingApiKey = controls.apiKey?.value || "";
  if (controls.enabled) {
    controls.enabled.checked = normalized.enabled;
  }
  if (controls.mode) {
    controls.mode.value = normalized.mode;
  }
  if (controls.provider) {
    controls.provider.value = normalized.provider;
  }
  if (controls.interventionLevel) {
    controls.interventionLevel.value = String(normalized.interventionLevel);
  }
  if (controls.apiKey) {
    controls.apiKey.value = normalized.apiKey || (preserveApiKey ? existingApiKey : "");
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
    controls.interventionLevel,
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
  if (controls.apiKey) {
    controls.apiKey.disabled = isLocked;
  }
  controls.enabled.disabled = isLocked;
  [controls.openAiPresetButton, controls.geminiPresetButton, controls.clearApiKeyButton].forEach((button) => {
    if (button) {
      button.disabled = isLocked || (button === controls.clearApiKeyButton && !(controls.apiKey?.value || "").trim());
    }
  });

  if (!helper) {
    renderAiQuickPanel();
    return;
  }

  const config = readAiFallbackForm();
  const providerInfo = state.aiCapabilities.providers?.[config.provider] || normalizeAiCapabilities(null).providers.openai;
  const apiKeyLabel = formatApiKeyEnvLabel(providerInfo);
  const hasProviderKey = hasLiveProviderKey(config, providerInfo);
  helper.classList.remove("is-warning", "is-success");

  if (isLocked) {
    helper.textContent = "내보내기 실행 중에는 AI 설정을 잠시 잠급니다.";
    renderAiQuickPanel();
    return;
  }

  if (!isEnabled || config.mode === "off") {
    helper.textContent = "AI 보정은 기본으로 꺼져 있습니다. 실행 후 애매한 페이지가 있으면 추천 신호만 표시됩니다.";
    renderAiQuickPanel();
    return;
  }

  if (!hasProviderKey) {
    helper.textContent = `${apiKeyLabel}를 환경변수, .env/.app_runtime/ai.env, 또는 위 입력칸으로 제공해야 ${aiProviderLabel(config.provider)} AI 보정을 실행할 수 있습니다. 입력한 키는 이 탭에서만 사용합니다.`;
    helper.classList.add("is-warning");
    renderAiQuickPanel();
    return;
  }

  if (config.mode === "force") {
    helper.textContent = `${aiInterventionLabel(config.interventionLevel)} · ${aiProviderLabel(config.provider)} ${config.model}로 모든 후보 페이지를 보정 시도합니다.`;
  } else {
    helper.textContent = `${aiInterventionLabel(config.interventionLevel)} · ${aiProviderLabel(config.provider)} ${config.model}로 권장된 페이지만 선택 보정합니다.`;
  }
  helper.classList.add("is-success");
  renderAiQuickPanel();
}

function applyAiPreset(provider) {
  const normalizedProvider = provider === "gemini" ? "gemini" : "openai";
  const preset = AI_PROVIDER_PRESETS[normalizedProvider] || AI_PROVIDER_PRESETS.openai;
  writeAiFallbackForm({
    enabled: true,
    mode: "auto",
    provider: preset.provider,
    model: preset.model,
    interventionLevel: preset.interventionLevel ?? DEFAULT_AI_FALLBACK.interventionLevel,
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
    card.innerHTML = `
      <div class="source-chip-head">
        <div>
          <div class="source-chip-name">${file.name}</div>
          <div class="source-chip-meta">
            <span>${index + 1}번째</span>
            <span>${isPdfFile(file) ? "PDF" : "이미지"}</span>
            <span>${formatFileSize(file.size)}</span>
          </div>
        </div>
        <span class="meta-pill">${isPdfFile(file) ? "PDF" : "사진"}</span>
      </div>
      <button class="small-button source-chip-remove" type="button" data-remove-file="${fileKey(file)}">제거</button>
    `;
    root.appendChild(card);
  });

  root.querySelectorAll("[data-remove-file]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextKey = button.dataset.removeFile;
      state.runSourceFiles = state.runSourceFiles.filter((file) => fileKey(file) !== nextKey);
      updateRuntimeControls();
      renderSourceQueue();
    });
  });
}

function updateQueuedFiles(fileList, { replace = false } = {}) {
  const incomingFiles = Array.from(fileList || []).filter(Boolean);
  if (!incomingFiles.length) {
    return;
  }

  const incomingMap = new Map(incomingFiles.map((file) => [fileKey(file), file]));
  const nextFiles = replace
    ? Array.from(incomingMap.values())
    : [...state.runSourceFiles.filter((file) => !incomingMap.has(fileKey(file))), ...incomingFiles];

  state.runSourceFiles = nextFiles;
  updateRuntimeControls();
  renderSourceQueue();
}

function clearQueuedFiles() {
  state.runSourceFiles = [];
  updateRuntimeControls();
  renderSourceQueue();
}

function clearAllState() {
  if (state.runBusy) {
    setRunStatus("현재 파싱 중에는 전체 초기화를 할 수 없습니다. 완료 후 다시 시도해주세요.", "warning");
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
      session_name: "새 세션",
      data_source: "empty",
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
const initialSession = generatedSession || createEmptySession();

const state = {
  session: initialSession,
  problems: cloneProblems(initialSession.problems),
  selectedId: initialSession.problems[0]?.id || null,
  previewMode: "problem",
  templateKey: "academy-default",
  dragId: null,
  composerOpen: false,
  apiAvailable: false,
  aiCapabilities: initialSession.aiCapabilities || normalizeAiCapabilities(null),
  runBusy: false,
  autoParse: true,
  runSourceFiles: [],
  runPresetKey: "default-parse",
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
  state.aiCapabilities = session.aiCapabilities?.provided ? session.aiCapabilities : state.aiCapabilities;
  state.problems = cloneProblems(session.problems);
  state.selectedId = session.problems.find((problem) => !problem.excluded)?.id || session.problems[0]?.id || null;
  state.composerOpen = false;
  state.runPresetKey = null;
  const layoutModeSelect = document.getElementById("runLayoutModeSelect");
  if (layoutModeSelect) {
    layoutModeSelect.value = session.exportMode || "question";
  }
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

  state.autoParse = true;
  if (autoParseToggle) {
    autoParseToggle.checked = true;
  }
  applyRunPreset("default-parse", { runAfterApply: false, silent: true });
}

function applyRunPreset(presetKey, options = {}) {
  const preset = RUN_PRESETS[presetKey];
  if (!preset) {
    return Promise.resolve();
  }

  const { runAfterApply = false, silent = false } = options;
  const runLayoutModeSelect = document.getElementById("runLayoutModeSelect");
  const runSubjectSelect = document.getElementById("runSubjectSelect");
  const runOcrSelect = document.getElementById("runOcrSelect");
  const runExportEdb = document.getElementById("runExportEdb");
  const currentAi = readAiFallbackForm();
  const provider = currentAi.provider || DEFAULT_AI_FALLBACK.provider;
  const providerPreset = AI_PROVIDER_PRESETS[provider] || AI_PROVIDER_PRESETS.openai;

  if (runLayoutModeSelect) {
    runLayoutModeSelect.value = preset.exportMode;
  }
  if (runSubjectSelect) {
    runSubjectSelect.value = preset.subject;
  }
  if (runOcrSelect) {
    runOcrSelect.value = preset.ocr;
  }
  if (runExportEdb) {
    runExportEdb.checked = Boolean(preset.exportEdb);
  }

  writeAiFallbackForm({
    enabled: Boolean(preset.ai.enabled),
    mode: preset.ai.mode,
    provider,
    apiKey: currentAi.apiKey,
    model: currentAi.model || providerPreset.model,
    interventionLevel: preset.ai.interventionLevel,
    threshold: currentAi.threshold,
    maxRegions: currentAi.maxRegions,
    timeoutMs: currentAi.timeoutMs,
    saveDebug: currentAi.saveDebug,
  }, { preserveApiKey: true });

  state.runPresetKey = presetKey;
  updateRuntimeControls();
  renderQuickRunPanel();

  if (!runAfterApply) {
    if (!silent) {
      setRunStatus(`${preset.label} 프리셋을 적용했습니다. ${state.runSourceFiles.length ? "현재 선택된 파일에 바로 적용할 수 있습니다." : "파일을 추가하면 이 설정으로 실행됩니다."}`, "neutral");
    }
    return Promise.resolve();
  }

  if (!state.runSourceFiles.length) {
    if (!silent) {
      setRunStatus(`${preset.label} 프리셋을 적용했습니다. 이제 파일만 추가하면 바로 실행됩니다.`, "neutral");
    }
    return Promise.resolve();
  }

  if (!state.apiAvailable) {
    if (!silent) {
      setRunStatus(`${preset.label} 프리셋을 적용했습니다. 로컬 앱 서버 연결 뒤 실행할 수 있습니다.`, "warning");
    }
    return Promise.resolve();
  }

  return runExportFromApi();
}

function clearRunPresetSelection() {
  if (!state.runPresetKey) {
    return;
  }
  state.runPresetKey = null;
  renderQuickRunPanel();
}

function renderQuickRunPanel() {
  const badge = document.getElementById("quickRunPresetBadge");
  const helper = document.getElementById("quickRunHelper");
  const activePreset = state.runPresetKey ? RUN_PRESETS[state.runPresetKey] : null;

  document.querySelectorAll("[data-run-preset]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.runPreset === state.runPresetKey);
    button.disabled = state.runBusy;
  });
  ["quickUseGeneratedButton", "quickUseSampleButton", "quickOpenAdvancedButton"].forEach((id) => {
    const button = document.getElementById(id);
    if (button) {
      button.disabled = state.runBusy;
    }
  });

  if (badge) {
    badge.textContent = activePreset ? activePreset.badge : "사용자 지정 설정";
  }
  if (helper) {
    const queueHint = state.runSourceFiles.length
      ? `현재 ${state.runSourceFiles.length}개 파일이 선택되어 있어 프리셋 버튼을 누르면 즉시 실행됩니다.`
      : "먼저 파일을 추가하면 프리셋 버튼으로 바로 실행할 수 있습니다.";
    helper.innerHTML = `${queueHint} 단축키: <kbd>Ctrl</kbd>/<kbd>Cmd</kbd> + <kbd>O</kbd> 파일 열기, <kbd>Ctrl</kbd>/<kbd>Cmd</kbd> + <kbd>Enter</kbd> 현재 설정 실행`;
  }
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

  apiStatus.textContent = state.apiAvailable ? "로컬 앱 연결됨" : "오프라인 미리보기";
  apiStatus.classList.toggle("is-connected", state.apiAvailable);
  apiStatus.classList.toggle("is-offline", !state.apiAvailable);

  selectedSourceName.textContent = summarizeQueuedSources();
  sourceQueueCount.textContent = state.runSourceFiles.length
    ? `${state.runSourceFiles.length}개 선택됨`
    : "0개 선택됨";
  runExportButton.disabled = !state.apiAvailable || state.runBusy || !state.runSourceFiles.length;
  if (clearAllButton) {
    clearAllButton.disabled = state.runBusy;
  }
  if (state.runBusy) {
    runExportButton.textContent = "실행 중...";
    runExportButton.title = "현재 파싱 작업을 실행 중입니다.";
  } else if (state.runPresetKey && RUN_PRESETS[state.runPresetKey]) {
    runExportButton.textContent = `${RUN_PRESETS[state.runPresetKey].label} 실행`;
    runExportButton.title = RUN_PRESETS[state.runPresetKey].description;
  } else {
    runExportButton.textContent = `${exportModeLabel(runLayoutModeSelect?.value)} 변환`;
    runExportButton.title = "현재 선택된 설정으로 파싱을 실행합니다.";
  }
  autoParseToggle.checked = state.autoParse;
  syncAiFallbackControls();
  renderQuickRunPanel();
}

async function probeApi() {
  try {
    const response = await fetch("/api/health");
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(`health ${response.status}`);
    }
    state.apiAvailable = true;
    state.aiCapabilities = normalizeAiCapabilities(
      payload.ai_capabilities
        || payload.aiCapabilities
        || payload.session?.ai_capabilities
        || payload.session?.aiCapabilities,
    );
    setRunStatus("로컬 파싱 API에 연결되었습니다. 사진을 올리면 바로 실행할 수 있습니다.", "success");
  } catch (error) {
    state.apiAvailable = false;
    setRunStatus("정적 미리보기 모드입니다. `app_server.py`를 실행하면 브라우저에서 바로 파싱할 수 있습니다.", "warning");
  }
  updateRuntimeControls();
}

async function fetchLatestSessionFromApi() {
  const response = await fetch("/api/session/latest");
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(buildApiErrorMessage(payload, `최근 세션을 불러오지 못했습니다 (${response.status})`));
  }
  return normalizeSession(payload.session, "최근 세션");
}

async function runExportFromApi() {
  if (!state.apiAvailable) {
    setRunStatus("로컬 파싱 API가 연결되지 않았습니다. 먼저 `app_server.py`를 실행해주세요.", "warning");
    return;
  }
  if (!state.runSourceFiles.length) {
    setRunStatus("먼저 사진이나 PDF를 선택해주세요.", "warning");
    return;
  }

  const runExportButton = document.getElementById("runExportButton");
  try {
    state.runBusy = true;
    runExportButton.disabled = true;
    setRunStatus(`소스 ${state.runSourceFiles.length}개를 업로드하고 자동 파싱하는 중입니다...`, "loading");

    const queue = [...state.runSourceFiles];
    const containsPhoto = queue.some((file) => !isPdfFile(file));
    const aiFallback = readAiFallbackForm();
    const filesPayload = await Promise.all(
      queue.map(async (file) => ({
        fileName: file.name,
        fileDataBase64: await fileToBase64(file),
      })),
    );

    const payload = {
      files: filesPayload,
      outputDir: document.getElementById("outputDirInput").value.trim(),
      exportMode: document.getElementById("runLayoutModeSelect").value,
      subject: document.getElementById("runSubjectSelect").value,
      ocr: document.getElementById("runOcrSelect").value,
      exportEdb: document.getElementById("runExportEdb").checked,
      detectPerspective: containsPhoto,
      maxDimension: containsPhoto ? 2400 : null,
      aiFallback: {
        enabled: aiFallback.enabled,
        mode: aiFallback.mode,
        provider: aiFallback.provider,
        model: aiFallback.model,
        apiKey: aiFallback.apiKey,
        interventionLevel: aiFallback.interventionLevel,
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
      throw new Error(buildApiErrorMessage(result, `파싱 실행 실패 (${response.status})`));
    }

    state.previewMode = "problem";
    state.aiCapabilities = normalizeAiCapabilities(
      result.ai_capabilities
        || result.aiCapabilities
        || result.session?.ai_capabilities
        || result.session?.aiCapabilities,
    );
    const normalizedSession = normalizeSession(result.session, "파싱 세션");
    applySession(normalizedSession);
    clearQueuedFiles();
    setRunStatus(
      `파싱 완료: ${exportModeLabel(normalizedSession.exportMode)} · ${normalizedSession.sourcePageCount || 0}페이지 · ${normalizedSession.detectedProblemCount || normalizedSession.problems.length}문항`,
      "success",
    );
  } catch (error) {
    setRunStatus(`파싱 실패: ${error.message}`, "error");
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
    const boardPreviewPath = item.boardRenderPath || item.imagePath || item.cropPath;
    card.innerHTML = `
      <div class="board-card-header">
        <strong>${cardTitle}</strong>
        <span>${item.subject}</span>
      </div>
      <img src="${boardPreviewPath}" alt="${cardTitle}">
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
        <h3>아직 파싱 결과가 없습니다</h3>
        <p class="helper-text">사진이나 PDF를 추가한 뒤 파싱을 실행하면 여기에서 문항 정보와 경고를 함께 검토할 수 있습니다.</p>
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
      <p class="helper-text">현재 조정은 브라우저 미리보기 기준입니다. 실제 EDB를 다시 만들려면 export를 다시 실행해야 합니다.</p>
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

    ${(aiFallback.provided || aiSummary.requested || aiSummary.attemptedPageCount > 0 || aiSummary.appliedPageCount > 0 || aiSummary.recommendedPageCount > 0 || aiSummary.localRetryRecommendedPageCount > 0) ? `
    <div class="inspector-card">
      <h3>AI 문항 보정</h3>
      <div class="inspector-row">
        <label>설정</label>
        <span>${aiInterventionLabel(aiFallback.interventionLevel)} · ${aiModeLabel(aiFallback.mode)} · ${aiProviderLabel(aiFallback.provider)} · ${aiFallback.model}</span>
      </div>
      <div class="inspector-row">
        <label>권장</label>
        <span>${formatAiRecommendationSummary(aiSummary) || "권장 없음"}</span>
      </div>
      <div class="inspector-row">
        <label>페이지</label>
        <span>시도 ${aiSummary.attemptedPageCount}p / 적용 ${aiSummary.appliedPageCount}p</span>
      </div>
      <div class="inspector-row">
        <label>출력 프로필</label>
        <span>${selected.aiInterventionLabel || aiInterventionLabel(selected.aiInterventionLevel || aiFallback.interventionLevel)}${selected.renderScaleFactor > 1 ? ` · ${selected.renderScaleFactor.toFixed(1)}x` : ""}</span>
      </div>
      <div class="inspector-row">
        <label>상태</label>
        <span>${formatAiStatusCounts(aiSummary.statusCounts) || "기록 없음"}</span>
      </div>
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
          <p class="subtle">순서 변경, 제목 수정, 제외/복원, 삭제를 미리보기 기준으로 조정할 수 있습니다. 실제 파일 반영은 다음 export 때 이루어집니다.</p>
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
  if (!inputCount && !pageCount && !problemCount) {
    node.textContent = "아직 파싱 결과가 없습니다. 사진이나 PDF를 추가해 첫 실행을 시작하세요. 필요하면 샘플 데이터 버튼으로 UI만 먼저 확인할 수 있습니다.";
    return;
  }
  const includedCount = activeProblems().length;
  const excludedCount = state.problems.length - includedCount;
  const aiSnapshot = getAiUiSnapshot();
  const aiSummary = aiSnapshot.sessionSummary;
  const aiFallback = aiSnapshot.liveConfig.enabled ? aiSnapshot.liveConfig : aiSnapshot.sessionFallback;
  let text = `${exportModeLabel(state.session.exportMode)} 변환 · 입력 ${inputCount}개 · 렌더 페이지 ${pageCount}개 · 감지 문항 ${problemCount}개 · 현재 포함 ${includedCount}개`;
  if (excludedCount > 0) {
    text += ` · 제외 ${excludedCount}개`;
  }
  const recommendationText = formatAiRecommendationSummary(aiSummary);
  if (aiFallback.enabled || aiSummary.requested) {
    text += ` · AI ${aiInterventionLabel(aiFallback.interventionLevel)} · ${aiModeLabel(aiFallback.mode)} ${aiProviderLabel(aiFallback.provider)}`;
    if (aiSummary.attemptedPageCount > 0 || aiSummary.appliedPageCount > 0) {
      text += ` · 시도 ${aiSummary.attemptedPageCount}p · 적용 ${aiSummary.appliedPageCount}p`;
    }
    const statusText = formatAiStatusCounts(aiSummary.statusCounts);
    if (statusText) {
      text += ` · ${statusText}`;
    }
  } else if (recommendationText) {
    text += ` · ${recommendationText}`;
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
  renderAiQuickPanel();

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
    document.getElementById("previewTitle").textContent = "아직 파싱 결과가 없습니다";
    document.getElementById("previewSubtitle").textContent = "사진이나 PDF를 추가해 첫 파싱을 시작하거나 샘플 데이터를 눌러 미리보기를 확인하세요.";
    document.getElementById("previewSurface").innerHTML = `
      <div class="preview-card">
        <div class="preview-image-frame preview-empty">
          <p class="helper-text">업로드 후 파싱을 실행하면 문항 크롭과 보드 미리보기가 여기에 표시됩니다.</p>
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

async function loadLatestSessionIntoView() {
  if (!state.apiAvailable) {
    setRunStatus("로컬 앱 서버가 연결되어 있지 않습니다. `app_server.py`를 실행한 뒤 다시 시도해주세요.", "warning");
    return;
  }
  try {
    applySession(await fetchLatestSessionFromApi());
    setRunStatus("로컬 앱 서버에서 최근 세션을 불러왔습니다.", "success");
  } catch (error) {
    setRunStatus(`최근 세션 불러오기 실패: ${error.message}`, "error");
  }
}

function switchToSampleSession() {
  applySession(sampleSession);
  setRunStatus("번들된 샘플 데이터로 전환했습니다. 이 화면은 실제 export 결과와 분리된 미리보기입니다.", "neutral");
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
  clearRunPresetSelection();
  updateRuntimeControls();
});
["runSubjectSelect", "runOcrSelect", "runExportEdb"].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", () => {
    clearRunPresetSelection();
    updateRuntimeControls();
  });
});

[
  "runAiFallbackEnabled",
  "runAiFallbackModeSelect",
  "runAiProviderSelect",
  "runAiInterventionLevelSelect",
  "runAiModelInput",
  "runAiThresholdInput",
  "runAiMaxRegionsInput",
  "runAiTimeoutInput",
  "runAiSaveDebug",
].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", () => {
    clearRunPresetSelection();
    syncAiFallbackControls();
  });
});
document.getElementById("runAiModelInput")?.addEventListener("input", () => {
  clearRunPresetSelection();
  syncAiFallbackControls();
});
document.getElementById("runAiApiKeyInput")?.addEventListener("input", syncAiFallbackControls);
document.getElementById("applyOpenAiPresetButton")?.addEventListener("click", () => applyAiPreset("openai"));
document.getElementById("applyGeminiPresetButton")?.addEventListener("click", () => applyAiPreset("gemini"));
document.getElementById("clearAiApiKeyButton")?.addEventListener("click", () => {
  const input = document.getElementById("runAiApiKeyInput");
  if (input) {
    input.value = "";
  }
  syncAiFallbackControls();
});
document.getElementById("openAiAdvancedButton")?.addEventListener("click", openAiAdvancedSettings);
document.getElementById("advancedSettingsPanel")?.addEventListener("toggle", renderAiQuickPanel);
document.getElementById("quickOpenAdvancedButton")?.addEventListener("click", openAiAdvancedSettings);
document.querySelectorAll("[data-run-preset]").forEach((button) => {
  button.addEventListener("click", async () => {
    await applyRunPreset(button.dataset.runPreset, { runAfterApply: true });
  });
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
    setRunStatus(`세션 JSON을 불러왔습니다: ${file.name}`, "success");
  } catch (error) {
    setRunStatus(`세션 JSON을 불러오지 못했습니다: ${error.message}`, "error");
  } finally {
    sessionFileInput.value = "";
  }
});

document.getElementById("useGeneratedButton").addEventListener("click", loadLatestSessionIntoView);
document.getElementById("quickUseGeneratedButton")?.addEventListener("click", loadLatestSessionIntoView);

document.getElementById("useSampleButton").addEventListener("click", switchToSampleSession);
document.getElementById("quickUseSampleButton")?.addEventListener("click", switchToSampleSession);

document.getElementById("clearAllButton").addEventListener("click", clearAllState);

const sourceFileInput = document.getElementById("sourceFileInput");
const cameraFileInput = document.getElementById("cameraFileInput");
const sourceDropzone = document.getElementById("sourceDropzone");

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

sourceFileInput.addEventListener("change", async (event) => {
  updateQueuedFiles(event.target.files, { replace: false });
  event.target.value = "";
  await maybeAutoRun();
});

cameraFileInput.addEventListener("change", async (event) => {
  updateQueuedFiles(event.target.files, { replace: false });
  event.target.value = "";
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
  updateQueuedFiles(event.dataTransfer?.files, { replace: false });
  await maybeAutoRun();
});

document.getElementById("runExportButton").addEventListener("click", runExportFromApi);

function isEditableTarget(target) {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.composerOpen) {
    closeComposerModal();
    return;
  }
  if (isEditableTarget(event.target)) {
    return;
  }
  const hasModifier = event.metaKey || event.ctrlKey;
  if (!hasModifier) {
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    void runExportFromApi();
    return;
  }
  if (event.key.toLowerCase() === "o") {
    event.preventDefault();
    sourceFileInput.click();
  }
});

async function initializeRuntimeConnection() {
  writeAiFallbackForm(state.session.aiFallback);
  syncAiFallbackControls();
  syncTemplateSelect();
  render();
  await probeApi();
  if (!state.apiAvailable) {
    return;
  }
  try {
    const latestSession = await fetchLatestSessionFromApi();
    applySession(latestSession);
    setRunStatus("로컬 앱 서버에서 최근 세션을 불러왔습니다.", "success");
  } catch (error) {
    setRunStatus("로컬 앱 서버가 연결되었습니다. 사진이나 PDF를 넣어 첫 파싱을 시작하세요.", "neutral");
  }
}

initializeRuntimeConnection();
