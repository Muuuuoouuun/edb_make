(function startTrialPage() {
  "use strict";

  const logic = window.TRIAL_LOGIC;
  const demo = document.body.dataset.trialMode === "demo" ? window.TRIAL_DEMO : null;
  const TURNSTILE_SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
  // Long enough for a visitor to notice and finish an interactive challenge.
  const TOKEN_WAIT_MS = 60000;
  const CONFIG_TIMEOUT_MS = 15000;
  // The server allows up to 60 seconds; leave room for upload and response transfer.
  const PARSE_TIMEOUT_MS = 90000;

  const state = {
    config: { inquiry_url: "https://classin.co.kr/contact", turnstile_site_key: null, max_bytes: 4000000, max_pages: 4, daily_limit: 3 },
    widgetId: null,
    token: null,
    tokenWaiters: [],
    tokenError: false,
    busy: false,
    configReady: null,
    retryFile: null,
    lastPayload: null,
    popupContext: {},
    previewMode: "raw",
  };

  const $ = id => document.getElementById(id);
  const views = { upload: $("view-upload"), processing: $("view-processing"), result: $("view-result") };

  function showView(name) {
    for (const [key, element] of Object.entries(views)) {
      element.hidden = key !== name;
    }
    window.scrollTo({ top: 0 });
  }

  function showUploadMessage(message) {
    const element = $("upload-message");
    element.textContent = message || "";
    element.hidden = !message;
    if (message) {
      showUploadHint("");
    }
  }

  function showUploadHint(message) {
    const element = $("upload-hint");
    element.textContent = message || "";
    element.hidden = !message;
  }

  function offerRetry(file, mayHaveUsedTrial = false) {
    state.retryFile = file || null;
    $("upload-retry").hidden = !file;
    $("upload-retry").title = file ? file.name : "";
    $("retry-notice").hidden = !file || !mayHaveUsedTrial || !!demo;
  }

  function setBusy(busy) {
    state.busy = busy;
    $("file-input").disabled = busy;
    $("upload-retry").disabled = busy;
    $("dropzone").setAttribute("aria-busy", String(busy));
    if (demo) demo.setBusy(busy);
  }

  async function requestText(url, options, timeout) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      // Include response-body transfer in the deadline too.
      return { ok: response.ok, status: response.status, text: await response.text() };
    } finally {
      clearTimeout(timer);
    }
  }

  function sendEvent(feature, action) {
    if (demo) return; // Keep event demonstrations out of the public conversion funnel.
    const body = JSON.stringify({ feature, action });
    try {
      if (navigator.sendBeacon && navigator.sendBeacon("/api/event", new Blob([body], { type: "text/plain" }))) {
        return;
      }
    } catch (error) {
      // fall through to fetch
    }
    fetch("/api/event", { method: "POST", body, keepalive: true, headers: { "content-type": "text/plain" } }).catch(() => {});
  }

  function openPremium(feature, context) {
    const content = logic.popupContent(feature, { mb: Math.floor(state.config.max_bytes / 1000000), max: state.config.max_pages, ...context });
    $("premium-badge").textContent = content.badge;
    $("premium-title").textContent = content.title;
    $("premium-body").textContent = content.body;
    const inquiry = $("premium-inquiry");
    inquiry.textContent = content.inquiryLabel;
    inquiry.href = state.config.inquiry_url;
    inquiry.dataset.feature = content.feature;
    $("premium-close").textContent = content.closeLabel;
    const dialog = $("premium-dialog");
    if (!dialog.open) {
      dialog.showModal();
    }
    sendEvent(content.feature, "open");
  }

  function resolveTokenWaiters(token) {
    const waiters = state.tokenWaiters.splice(0);
    for (const resolve of waiters) {
      resolve(token);
    }
  }

  function waitForToken() {
    if (!state.config.turnstile_site_key) {
      return Promise.resolve(null);
    }
    if (state.token) {
      return Promise.resolve(state.token);
    }
    if (state.tokenError) {
      return Promise.resolve(null);
    }
    return new Promise(resolve => {
      const finish = token => {
        clearTimeout(timer);
        state.tokenWaiters = state.tokenWaiters.filter(waiter => waiter !== finish);
        resolve(token);
      };
      const timer = setTimeout(() => finish(state.token), TOKEN_WAIT_MS);
      state.tokenWaiters.push(finish);
    });
  }

  function resetToken() {
    state.token = null;
    state.tokenError = false;
    if (window.turnstile && state.widgetId !== null) {
      try {
        window.turnstile.reset(state.widgetId);
      } catch (error) {
        state.tokenError = true;
      }
    }
  }

  function loadTurnstile(siteKey) {
    state.tokenError = false;
    const script = document.createElement("script");
    script.src = TURNSTILE_SCRIPT;
    script.async = true;
    const failed = () => {
      state.tokenError = true;
      state.token = null;
      resolveTokenWaiters(null);
      showUploadMessage("확인 도구에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.");
    };
    script.onload = () => {
      try {
        state.widgetId = window.turnstile.render("#turnstile-widget", {
          sitekey: siteKey,
          action: "parse",
          language: "ko",
          callback: token => {
            state.token = token;
            state.tokenError = false;
            resolveTokenWaiters(token);
          },
          "expired-callback": () => {
            state.token = null;
          },
          "error-callback": failed,
        });
      } catch (error) {
        failed();
      }
    };
    script.onerror = failed;
    document.head.appendChild(script);
  }

  async function loadConfig() {
    try {
      if (demo) {
        const config = await demo.initialize(() => {
          state.lastPayload = null;
          offerRetry(null);
          showUploadMessage("");
          showUploadHint("");
          $("pages").replaceChildren();
          $("problems").replaceChildren();
          $("preview-toggle").hidden = true;
          $("file-input").value = "";
          if ($("premium-dialog").open) $("premium-dialog").close();
          showView("upload");
        });
        state.config = { ...state.config, ...config, turnstile_site_key: null };
      } else {
        const response = await requestText("/api/config", { cache: "no-store" }, CONFIG_TIMEOUT_MS);
        if (!response.ok) throw new Error("config unavailable");
        const config = JSON.parse(response.text);
        if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("invalid config");
        state.config = { ...state.config, ...config };
      }
    } catch (error) {
      showUploadMessage("체험 정보를 불러오지 못했어요. 연결을 확인한 뒤 다시 시도해 주세요.");
      return false;
    }
    const mb = Math.floor(state.config.max_bytes / 1000000);
    $("limits-text").textContent = demo
      ? `앞 ${state.config.max_pages}쪽 · ${mb}MB · 시연 기간에는 반복 이용 가능`
      : `앞 ${state.config.max_pages}쪽 · ${mb}MB · 하루 ${state.config.daily_limit}회`;
    $("header-inquiry").href = state.config.inquiry_url;
    if (state.config.turnstile_site_key) {
      loadTurnstile(state.config.turnstile_site_key);
    }
    return true;
  }

  function startElapsedTimer() {
    const startedAt = performance.now();
    const label = $("processing-elapsed");
    label.textContent = "0초";
    const timer = setInterval(() => {
      label.textContent = `${Math.floor((performance.now() - startedAt) / 1000)}초`;
    }, 500);
    return () => clearInterval(timer);
  }

  async function handleFile(file) {
    // Clear on every path so choosing the same file again fires "change" again.
    $("file-input").value = "";
    if (state.busy || !file) {
      return;
    }
    setBusy(true);
    offerRetry(null);
    let stopTimer = () => {};
    let parseStarted = false;
    try {
      // Without the config we do not know whether a Turnstile token is required.
      showUploadMessage("");
      showUploadHint("체험 정보를 확인하고 있어요.");
      if (!(await state.configReady)) {
        state.configReady = loadConfig();
        if (!(await state.configReady)) {
          offerRetry(file);
          return;
        }
      }
      if (demo && !demo.canParse()) return;
      showUploadMessage("");
      showUploadHint("");
      const precheck = logic.precheckFile(file, state.config);
      if (precheck) {
        if (precheck.feature) {
          openPremium(precheck.feature);
        } else {
          showUploadMessage(precheck.message);
        }
        return;
      }

      // Wait on the upload view: the widget lives there and may need a click.
      if (state.config.turnstile_site_key && state.tokenError) {
        if (state.widgetId !== null) resetToken();
        else loadTurnstile(state.config.turnstile_site_key);
      }
      if (state.config.turnstile_site_key && !state.token) {
        showUploadHint("아래 확인을 완료하면 바로 시작해요.");
      }
      const token = await waitForToken();
      showUploadHint("");
      if (state.config.turnstile_site_key && !token) {
        showUploadMessage(state.tokenError
          ? "확인 도구에 연결하지 못했어요. 잠시 후 다시 시도해 주세요."
          : "사람 확인이 아직 끝나지 않았어요. 확인 상자를 완료한 뒤 다시 시도해 주세요.");
        offerRetry(file);
        return;
      }

      $("processing-file").textContent = file.name;
      showView("processing");
      stopTimer = startElapsedTimer();
      let status = 0;
      let text = "";
      let timedOut = false;
      try {
        const headers = { "content-type": "application/pdf" };
        if (token) {
          headers["x-turnstile-token"] = token;
        }
        if (demo) headers["X-Demo-Request"] = "1";
        parseStarted = true;
        const response = await requestText(demo ? "/api/demo/parse" : "/api/parse", {
          method: "POST", body: file, headers, credentials: "same-origin",
        }, PARSE_TIMEOUT_MS);
        status = response.status;
        text = response.text;
      } catch (error) {
        status = 0;
        timedOut = error.name === "AbortError";
      } finally {
        resetToken();
      }

      if (demo && (demo.handleResponse(status, text) || !demo.canParse())) return;
      if (status === 200) {
        let payload = null;
        try {
          payload = JSON.parse(text);
        } catch (error) {
          payload = null;
        }
        if (logic.isValidResultPayload(payload)) {
          renderResult(payload);
          return;
        }
      }
      const problem = logic.interpretError(status, text);
      showView("upload");
      // Say what happened even when a premium popup follows, e.g. a parse failure.
      showUploadMessage(timedOut
        ? "처리 시간이 길어져 연결을 마쳤어요. 잠시 후 다시 시도해 주세요."
        : status === 200 ? "처리 결과를 불러오지 못했어요. 다시 시도해 주세요." : problem.message);
      if (status === 0 || status === 200 || status >= 500) offerRetry(file, parseStarted);
      if (problem.feature) {
        openPremium(problem.feature);
      }
    } catch (error) {
      showView("upload");
      showUploadMessage("처리 결과를 불러오지 못했어요. 다시 시도해 주세요.");
      offerRetry(file, parseStarted);
    } finally {
      stopTimer();
      showUploadHint("");
      setBusy(false);
      $("file-input").value = "";
    }
  }

  function setActive(problemId, source) {
    for (const element of document.querySelectorAll("[data-problem-id]")) {
      element.classList.toggle("is-active", element.dataset.problemId === problemId);
    }
    const target =
      source === "region"
        ? document.querySelector(`.problem-card[data-problem-id="${CSS.escape(problemId)}"]`)
        : document.querySelector(`.region[data-problem-id="${CSS.escape(problemId)}"]`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function renderPages(payload) {
    const container = $("pages");
    container.replaceChildren();
    const pagesById = new Map();
    for (const page of payload.pages) {
      const figure = document.createElement("figure");
      figure.className = "page-figure";
      if (logic.isSafeImageSource(page.preview)) {
        const image = document.createElement("img");
        image.src = page.preview;
        image.alt = `${page.index + 1}쪽 미리보기`;
        image.width = page.width;
        image.height = page.height;
        figure.appendChild(image);
      }
      const caption = document.createElement("figcaption");
      caption.textContent = `${page.index + 1}쪽`;
      figure.appendChild(caption);
      container.appendChild(figure);
      pagesById.set(page.page_id, { page, figure });
    }
    for (const problem of payload.problems) {
      const label = logic.problemLabel(problem);
      problem.regions.forEach((region, regionIndex) => {
        const target = pagesById.get(region.page_id);
        if (!target) {
          return;
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = problem.number === null ? "region region--passage" : "region";
        button.dataset.problemId = problem.problem_id;
        button.setAttribute("aria-label", `${label} 영역`);
        Object.assign(button.style, logic.regionStyle(region.bbox, target.page));
        if (regionIndex === 0) {
          const tag = document.createElement("span");
          tag.className = "region__label";
          tag.textContent = label;
          button.appendChild(tag);
        }
        button.addEventListener("click", () => setActive(problem.problem_id, "region"));
        target.figure.appendChild(button);
      });
    }
  }

  function isBoardSource(problem, source) {
    return state.previewMode === "board" && source !== null && source === problem.board;
  }

  function syncPreviewToggle(payload) {
    const toggle = $("preview-toggle");
    const available = logic.hasBoardPreviews(payload);
    if (!available) {
      state.previewMode = "raw";
    }
    toggle.hidden = !available;
    for (const button of toggle.querySelectorAll("[data-mode]")) {
      button.setAttribute("aria-pressed", String(button.dataset.mode === state.previewMode));
    }
  }

  function setPreviewMode(mode) {
    if ((mode !== "raw" && mode !== "board") || mode === state.previewMode) {
      return;
    }
    state.previewMode = mode;
    const payload = state.lastPayload;
    if (payload) {
      const byId = new Map(payload.problems.map(problem => [problem.problem_id, problem]));
      for (const item of $("problems").querySelectorAll(".problem-card")) {
        const problem = byId.get(item.dataset.problemId);
        const image = item.querySelector("img");
        if (!problem || !image) {
          continue;
        }
        const source = logic.cardImageSource(problem, state.previewMode);
        if (source) {
          image.src = source;
        }
        item.classList.toggle("problem-card--board", isBoardSource(problem, source));
      }
    }
    syncPreviewToggle(payload);
  }

  function renderProblems(payload) {
    const list = $("problems");
    list.replaceChildren();
    for (const problem of payload.problems) {
      const item = document.createElement("li");
      item.className = "problem-card";
      item.dataset.problemId = problem.problem_id;
      item.tabIndex = 0;

      const head = document.createElement("div");
      head.className = "problem-card__head";
      const label = document.createElement("span");
      label.className = "problem-card__label";
      label.textContent = logic.problemLabel(problem);
      head.appendChild(label);
      if (problem.needs_review) {
        const chip = document.createElement("span");
        chip.className = "review-chip";
        chip.textContent = "확인 필요";
        head.appendChild(chip);
      }
      item.appendChild(head);

      const source = logic.cardImageSource(problem, state.previewMode);
      if (source) {
        const image = document.createElement("img");
        image.src = source;
        image.alt = `${logic.problemLabel(problem)} 미리보기`;
        image.loading = "lazy";
        item.appendChild(image);
      }
      item.classList.toggle("problem-card--board", isBoardSource(problem, source));

      if (problem.needs_review) {
        const ai = document.createElement("button");
        ai.type = "button";
        ai.className = "text-button problem-card__ai";
        ai.textContent = "AI로 더 정확하게 ✦";
        ai.addEventListener("click", event => {
          event.stopPropagation();
          openPremium("ai");
        });
        item.appendChild(ai);
      }

      const activate = () => setActive(problem.problem_id, "card");
      item.addEventListener("click", activate);
      item.addEventListener("keydown", event => {
        // Keys pressed on buttons inside the card belong to those buttons.
        if (event.target !== item) {
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });
      list.appendChild(item);
    }
  }

  function renderResult(payload) {
    state.lastPayload = payload;
    const summary = logic.summarize(payload);
    $("result-title").textContent = summary.headline;
    $("result-meta").textContent = `${summary.seconds} · 앞 ${payload.processed_page_count}쪽 처리`;
    $("remaining-text").textContent = demo
      ? "시연 기간에는 횟수 제한 없이 다시 이용할 수 있어요"
      : logic.remainingText(payload.remaining_today);

    const banner = logic.pagesBanner(payload);
    const bannerButton = $("pages-banner");
    bannerButton.hidden = !banner;
    if (banner) {
      bannerButton.textContent = `${banner.text} →`;
      state.popupContext = banner.context;
    }

    const empty = payload.problems.length === 0;
    $("empty-result").hidden = !empty;
    $("result-body").hidden = empty;
    syncPreviewToggle(payload);
    renderPages(payload);
    renderProblems(payload);
    showView("result");
    if (empty) {
      openPremium("ai");
    }
  }

  function bindEvents() {
    const input = $("file-input");
    input.addEventListener("change", () => handleFile(input.files && input.files[0]));

    const dropzone = $("dropzone");
    for (const type of ["dragenter", "dragover"]) {
      dropzone.addEventListener(type, event => {
        event.preventDefault();
        dropzone.classList.add("is-dragging");
      });
    }
    for (const type of ["dragleave", "drop"]) {
      dropzone.addEventListener(type, () => dropzone.classList.remove("is-dragging"));
    }
    // A file dropped anywhere must not make the browser navigate away to the PDF.
    document.addEventListener("dragover", event => {
      event.preventDefault();
    });
    document.addEventListener("drop", event => {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
      if (views.upload.hidden || state.busy) {
        return;
      }
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      handleFile(file);
    });

    document.addEventListener("click", event => {
      const trigger = event.target.closest("[data-premium]");
      if (trigger) {
        openPremium(trigger.dataset.premium);
      }
    });

    $("preview-toggle").addEventListener("click", event => {
      const button = event.target.closest("[data-mode]");
      if (button) {
        setPreviewMode(button.dataset.mode);
      }
    });
    $("pages-banner").addEventListener("click", () => openPremium("limit_pages", state.popupContext));
    $("premium-inquiry").addEventListener("click", event => sendEvent(event.currentTarget.dataset.feature || "ai", "inquiry"));
    $("retry-button").addEventListener("click", () => {
      showUploadMessage("");
      showView("upload");
    });
    $("upload-retry").addEventListener("click", () => handleFile(state.retryFile));

    const dialog = $("premium-dialog");
    dialog.addEventListener("click", event => {
      if (event.target === dialog) {
        dialog.close();
      }
    });
  }

  bindEvents();
  state.configReady = loadConfig();
})();
