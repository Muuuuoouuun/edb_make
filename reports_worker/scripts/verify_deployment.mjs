#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { REPORT_CONTRACT } from "../src/index.js";


const DEFAULT_HEALTH_URL = "https://reports.classin.cloud/health";
const WORKER_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WRANGLER_CONFIG_PATH = resolve(WORKER_ROOT, "wrangler.toml");
const REQUIRED_COLUMNS = Object.freeze([
  "id",
  "created_at",
  "app_id",
  "app_version",
  "platform",
  "category",
  "description",
  "context_json",
  "diagnostics_json",
  "status",
  "reporter_contact",
  "consent_to_contact",
  "error_code",
  "failed_operation",
  "resolution_note",
  "resolved_at",
  "payload_hash",
]);
const REQUIRED_UNIQUE_INDEXES = Object.freeze([
  "bug_reports_payload_hash_unique_idx",
]);
const SCHEMA_QUERY = [
  "SELECT 'column' AS kind, name, 0 AS is_unique, 0 AS is_partial,",
  "NULL AS indexed_column, NULL AS index_sql",
  "FROM pragma_table_info('bug_reports')",
  "UNION ALL",
  "SELECT 'index' AS kind, indexes.name, indexes.\"unique\" AS is_unique,",
  "indexes.partial AS is_partial, index_info.name AS indexed_column, schema.sql AS index_sql",
  "FROM pragma_index_list('bug_reports') AS indexes",
  "LEFT JOIN pragma_index_info('bug_reports_payload_hash_unique_idx') AS index_info",
  "ON indexes.name = 'bug_reports_payload_hash_unique_idx'",
  "LEFT JOIN sqlite_master AS schema ON schema.type = 'index' AND schema.name = indexes.name",
  "ORDER BY kind, name",
].join(" ");


function unwrapD1Rows(payload) {
  const results = Array.isArray(payload) ? payload : [payload];
  const rows = [];
  for (const result of results) {
    if (!result || result.success === false) {
      throw new Error(`D1 read-only schema query failed: ${JSON.stringify(result)}`);
    }
    if (Array.isArray(result.results)) rows.push(...result.results);
  }
  return rows;
}


function collectSchemaErrors(rows) {
  if (!Array.isArray(rows)) return ["D1 schema query did not return rows"];
  const columns = new Set(rows.filter(row => row?.kind === "column").map(row => row.name));
  const errors = [];
  for (const column of REQUIRED_COLUMNS) {
    if (!columns.has(column)) errors.push(`D1 bug_reports is missing column: ${column}`);
  }
  for (const index of REQUIRED_UNIQUE_INDEXES) {
    const indexRows = rows.filter(row => row?.kind === "index" && row.name === index);
    if (!indexRows.length || !indexRows.some(row => Number(row.is_unique) === 1)) {
      errors.push(`D1 bug_reports is missing unique index: ${index}`);
      continue;
    }
    const indexedColumns = new Set(
      indexRows.map(row => String(row.indexed_column || "").trim()).filter(Boolean),
    );
    if (indexedColumns.size !== 1 || !indexedColumns.has("payload_hash")) {
      errors.push(`D1 ${index} must index only payload_hash`);
    }
    if (!indexRows.some(row => Number(row.is_partial) === 1)) {
      errors.push(`D1 ${index} must be a partial index`);
    }
    const sql = String(indexRows.find(row => row.index_sql)?.index_sql || "")
      .replace(/["`\[\]]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    const requiredSql = new RegExp(
      "^CREATE\\s+UNIQUE\\s+INDEX\\s+(?:IF\\s+NOT\\s+EXISTS\\s+)?"
      + "bug_reports_payload_hash_unique_idx\\s+ON\\s+bug_reports\\s*"
      + "\\(\\s*payload_hash\\s*\\)\\s+WHERE\\s+payload_hash\\s+IS\\s+NOT\\s+NULL\\s*;?$",
      "i",
    );
    if (!requiredSql.test(sql)) {
      errors.push(
        `D1 ${index} SQL must exactly define the payload_hash IS NOT NULL partial predicate`,
      );
    }
  }
  return errors;
}


function collectHealthErrors(payload) {
  const errors = [];
  if (!payload || payload.service !== "classin-edb-reports") {
    return ["Worker health response does not identify classin-edb-reports"];
  }
  if (payload.ok !== true || payload.ready !== true || payload.readiness?.ready !== true) {
    errors.push("Worker health response is not deployment-ready");
  }
  for (const binding of REPORT_CONTRACT.requiredBindings) {
    if (payload.readiness?.bindings?.[binding] !== true) {
      errors.push(`Worker health response reports unavailable binding: ${binding}`);
    }
  }
  const contract = payload.reportContract;
  if (!contract || typeof contract !== "object" || Array.isArray(contract)) {
    return ["Worker health response is missing reportContract"];
  }
  for (const field of ["reportSchemaVersion", "receiptSchemaVersion", "contactAccepted", "idempotency"]) {
    if (contract[field] !== REPORT_CONTRACT[field]) {
      errors.push(`Worker reportContract.${field} mismatch`);
    }
  }
  for (const field of ["operationErrorFields", "requiredMigrations", "requiredBindings"]) {
    if (JSON.stringify(contract[field]) !== JSON.stringify(REPORT_CONTRACT[field])) {
      errors.push(`Worker reportContract.${field} mismatch`);
    }
  }
  return errors;
}


function parseWranglerJson(stdout) {
  const text = String(stdout || "").trim();
  try {
    return JSON.parse(text);
  } catch {
    const candidates = [text.indexOf("["), text.indexOf("{")].filter(index => index >= 0).sort((a, b) => a - b);
    for (const index of candidates) {
      try {
        return JSON.parse(text.slice(index));
      } catch {
        // Try the next JSON-looking section.
      }
    }
    throw new Error(`Wrangler did not return JSON: ${text.slice(-1_000)}`);
  }
}


function configuredReportsDatabase(configText) {
  const sections = String(configText || "")
    .split(/^\[\[d1_databases\]\]\s*$/m)
    .slice(1)
    .map(section => section.split(/^\[\[/m, 1)[0]);
  const values = sections.map(section => {
    const readString = key => {
      const match = section.match(new RegExp(`^\\s*${key}\\s*=\\s*["']([^"']+)["']\\s*$`, "m"));
      return match?.[1]?.trim() || "";
    };
    return {
      binding: readString("binding"),
      databaseName: readString("database_name"),
      databaseId: readString("database_id"),
    };
  });
  const targets = values.filter(value => value.binding === "REPORTS_DB");
  if (targets.length !== 1) {
    throw new Error("wrangler.toml must define exactly one REPORTS_DB D1 binding");
  }
  const target = targets[0];
  if (!target.databaseName || !target.databaseId) {
    throw new Error("REPORTS_DB must define database_name and database_id in wrangler.toml");
  }
  return target;
}


function querySchema(mode) {
  const target = configuredReportsDatabase(readFileSync(WRANGLER_CONFIG_PATH, "utf8"));
  const npx = process.platform === "win32" ? "npx.cmd" : "npx";
  const completed = spawnSync(
    npx,
    [
      "--no-install",
      "wrangler",
      "d1",
      "execute",
      target.databaseName,
      mode,
      "--command",
      SCHEMA_QUERY,
      "--json",
    ],
    {
      cwd: WORKER_ROOT,
      encoding: "utf8",
      maxBuffer: 2 * 1024 * 1024,
    },
  );
  if (completed.error || completed.status !== 0) {
    throw new Error(
      `Wrangler D1 read-only verification failed: ${completed.error || completed.stderr || completed.stdout}`,
    );
  }
  return unwrapD1Rows(parseWranglerJson(completed.stdout));
}


async function verifyHealth(endpoint) {
  const url = new URL(endpoint);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "::1"].includes(url.hostname))) {
    throw new Error("health endpoint must use HTTPS or loopback HTTP");
  }
  const response = await fetch(url, {
    method: "GET",
    headers: { accept: "application/json", "cache-control": "no-cache" },
    redirect: "error",
  });
  if (!response.ok) throw new Error(`Worker health check returned HTTP ${response.status}`);
  return response.json();
}


function parseArgs(argv) {
  const mode = argv.includes("--remote") ? "--remote" : argv.includes("--local") ? "--local" : "";
  if (!mode || (argv.includes("--remote") && argv.includes("--local"))) {
    throw new Error("choose exactly one of --remote or --local");
  }
  const phaseIndex = argv.indexOf("--phase");
  const phase = phaseIndex >= 0 ? argv[phaseIndex + 1] : "post";
  if (!["pre", "post"].includes(phase)) throw new Error("--phase must be pre or post");
  const endpointIndex = argv.indexOf("--endpoint");
  const endpoint = endpointIndex >= 0 ? argv[endpointIndex + 1] : DEFAULT_HEALTH_URL;
  if (!endpoint) throw new Error("--endpoint requires a value");
  return { mode, phase, endpoint };
}


async function main(argv = process.argv.slice(2)) {
  const { mode, phase, endpoint } = parseArgs(argv);
  const errors = collectSchemaErrors(querySchema(mode));
  if (phase === "post") {
    errors.push(...collectHealthErrors(await verifyHealth(endpoint)));
  }
  if (errors.length) throw new Error(errors.join("\n"));
  console.log(`[reports-deployment] OK: ${mode.slice(2)} ${phase} verification`);
}


if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    console.error(`[reports-deployment] ERROR: ${error.message}`);
    process.exitCode = 1;
  });
}


export {
  REQUIRED_COLUMNS,
  REQUIRED_UNIQUE_INDEXES,
  collectHealthErrors,
  collectSchemaErrors,
  configuredReportsDatabase,
  parseWranglerJson,
  unwrapD1Rows,
};
