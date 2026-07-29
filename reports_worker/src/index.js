const MAX_BODY_BYTES = 64 * 1024;
const MAX_DESCRIPTION_CHARS = 4_000;
const MAX_JSON_CHARS = 48_000;
const ALLOWED_APP_IDS = new Set(["ClassInEDBMVP"]);

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
    },
  });
}

function text(value, status = 200) {
  return new Response(value, {
    status,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
    },
  });
}

function cleanText(value, maxChars) {
  return String(value ?? "").trim().slice(0, maxChars);
}

function boundedJson(value, fallback = "{}") {
  try {
    const serialized = JSON.stringify(value ?? {});
    return serialized.length <= MAX_JSON_CHARS ? serialized : fallback;
  } catch {
    return fallback;
  }
}

function reportId(now = new Date()) {
  const date = now.toISOString().slice(0, 10).replaceAll("-", "");
  const bytes = new Uint8Array(5);
  crypto.getRandomValues(bytes);
  const suffix = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return `EDB-${date}-${suffix.toUpperCase()}`;
}

async function readJson(request) {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return { error: json({ ok: false, error: "content_type_required" }, 415) };
  }
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return { error: json({ ok: false, error: "payload_too_large" }, 413) };
  }
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return { error: json({ ok: false, error: "payload_too_large" }, 413) };
  }
  try {
    return { value: JSON.parse(raw) };
  } catch {
    return { error: json({ ok: false, error: "invalid_json" }, 400) };
  }
}

function validateReport(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "object_required";
  }
  if (payload.schemaVersion !== 1) {
    return "unsupported_schema";
  }
  const appId = cleanText(payload.app?.id, 80);
  if (!ALLOWED_APP_IDS.has(appId)) {
    return "unknown_app";
  }
  const description = cleanText(payload.description, MAX_DESCRIPTION_CHARS);
  if (description.length < 5) {
    return "description_too_short";
  }
  if (boundedJson(payload.context, "").length === 0) {
    return "invalid_context";
  }
  if (payload.diagnostics != null && boundedJson(payload.diagnostics, "").length === 0) {
    return "invalid_diagnostics";
  }
  return "";
}

async function createReport(request, env) {
  const parsed = await readJson(request);
  if (parsed.error) return parsed.error;
  const validationError = validateReport(parsed.value);
  if (validationError) {
    return json({ ok: false, error: validationError }, 400);
  }

  const payload = parsed.value;
  const createdAt = new Date().toISOString();
  const id = reportId(new Date(createdAt));
  const statement = env.REPORTS_DB.prepare(
    `INSERT INTO bug_reports (
      id, created_at, app_id, app_version, platform, category,
      description, context_json, diagnostics_json, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')`
  ).bind(
    id,
    createdAt,
    cleanText(payload.app.id, 80),
    cleanText(payload.app.version, 80) || "unknown",
    cleanText(payload.app.platform, 40) || "unknown",
    cleanText(payload.category, 40) || "bug",
    cleanText(payload.description, MAX_DESCRIPTION_CHARS),
    boundedJson(payload.context),
    payload.diagnostics == null ? null : boundedJson(payload.diagnostics),
  );
  await statement.run();
  return json({ ok: true, reportId: id, receivedAt: createdAt }, 201);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "classin-edb-reports" });
    }
    if (request.method === "GET" && url.pathname === "/") {
      return text("ClassIn EDB report collector");
    }
    if (request.method === "POST" && url.pathname === "/v1/edb-reports") {
      if (!env.REPORTS_DB) {
        return json({ ok: false, error: "storage_unavailable" }, 503);
      }
      try {
        return await createReport(request, env);
      } catch (error) {
        console.error("bug report storage failed", error);
        return json({ ok: false, error: "storage_failed" }, 500);
      }
    }
    return json({ ok: false, error: "not_found" }, 404);
  },
};

export {
  MAX_BODY_BYTES,
  createReport,
  reportId,
  validateReport,
};
