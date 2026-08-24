import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { REPORT_CONTRACT } from "../src/index.js";
import {
  REQUIRED_COLUMNS,
  REQUIRED_UNIQUE_INDEXES,
  collectHealthErrors,
  collectSchemaErrors,
  configuredReportsDatabase,
  parseWranglerJson,
  unwrapD1Rows,
} from "../scripts/verify_deployment.mjs";


const WORKER_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");


function validSchemaRows() {
  return [
    ...REQUIRED_COLUMNS.map(name => ({
      kind: "column",
      name,
      is_unique: 0,
      is_partial: 0,
      indexed_column: null,
      index_sql: null,
    })),
    ...REQUIRED_UNIQUE_INDEXES.map(name => ({
      kind: "index",
      name,
      is_unique: 1,
      is_partial: 1,
      indexed_column: "payload_hash",
      index_sql: (
        "CREATE UNIQUE INDEX bug_reports_payload_hash_unique_idx "
        + "ON bug_reports(payload_hash) WHERE payload_hash IS NOT NULL"
      ),
    })),
  ];
}


function readyHealthPayload() {
  return {
    ok: true,
    ready: true,
    service: "classin-edb-reports",
    readiness: {
      ready: true,
      bindings: {
        REPORTS_DB: true,
        REPORT_RATE_LIMITER: true,
      },
    },
    reportContract: REPORT_CONTRACT,
  };
}


test("deployment verifier accepts the required D1 and health contracts", () => {
  assert.deepEqual(collectSchemaErrors(validSchemaRows()), []);
  assert.deepEqual(collectHealthErrors(readyHealthPayload()), []);
});


test("deployment verifier catches contact, operation error, and dedupe migration gaps", () => {
  const rows = validSchemaRows().filter(row => ![
    "reporter_contact",
    "consent_to_contact",
    "error_code",
    "failed_operation",
    "payload_hash",
    "bug_reports_payload_hash_unique_idx",
  ].includes(row.name));
  const errors = collectSchemaErrors(rows);

  for (const expected of [
    "reporter_contact",
    "consent_to_contact",
    "error_code",
    "failed_operation",
    "payload_hash",
    "bug_reports_payload_hash_unique_idx",
  ]) {
    assert.ok(errors.some(error => error.includes(expected)), expected);
  }
});


test("deployment verifier rejects a stale Worker response contract", () => {
  const errors = collectHealthErrors({
    ok: true,
    ready: true,
    service: "classin-edb-reports",
  });
  assert.ok(errors.some(error => error.includes("reportContract")));
});


test("deployment verifier rejects unavailable Worker bindings", () => {
  const payload = readyHealthPayload();
  payload.ok = false;
  payload.ready = false;
  payload.readiness.ready = false;
  payload.readiness.bindings.REPORT_RATE_LIMITER = false;

  const errors = collectHealthErrors(payload);

  assert.ok(errors.some(error => error.includes("deployment-ready")));
  assert.ok(errors.some(error => error.includes("REPORT_RATE_LIMITER")));
});


test("deployment verifier checks payload hash index column and partial SQL", () => {
  const wrongColumnRows = validSchemaRows().map(row => (
    row.name === "bug_reports_payload_hash_unique_idx"
      ? { ...row, indexed_column: "description" }
      : row
  ));
  const missingWhereRows = validSchemaRows().map(row => (
    row.name === "bug_reports_payload_hash_unique_idx"
      ? {
          ...row,
          is_partial: 0,
          index_sql: "CREATE UNIQUE INDEX bug_reports_payload_hash_unique_idx ON bug_reports(payload_hash)",
        }
      : row
  ));
  const extraPredicateRows = validSchemaRows().map(row => (
    row.name === "bug_reports_payload_hash_unique_idx"
      ? {
          ...row,
          index_sql: (
            "CREATE UNIQUE INDEX bug_reports_payload_hash_unique_idx "
            + "ON bug_reports(payload_hash) WHERE payload_hash IS NOT NULL AND description != ''"
          ),
        }
      : row
  ));

  assert.ok(collectSchemaErrors(wrongColumnRows).some(error => error.includes("only payload_hash")));
  const missingWhereErrors = collectSchemaErrors(missingWhereRows);
  assert.ok(missingWhereErrors.some(error => error.includes("partial index")));
  assert.ok(missingWhereErrors.some(error => error.includes("partial predicate")));
  assert.ok(collectSchemaErrors(extraPredicateRows).some(error => error.includes("partial predicate")));
});


test("deployment verifier derives the target from the REPORTS_DB Wrangler binding", () => {
  const config = `
[[d1_databases]]
binding = "REPORTS_DB"
database_name = "configured-reports-db"
database_id = "11111111-2222-3333-4444-555555555555"
`;
  assert.deepEqual(configuredReportsDatabase(config), {
    binding: "REPORTS_DB",
    databaseName: "configured-reports-db",
    databaseId: "11111111-2222-3333-4444-555555555555",
  });
  assert.throws(
    () => configuredReportsDatabase(config.replace("REPORTS_DB", "WRONG_BINDING")),
    /exactly one REPORTS_DB/,
  );
  const actualTarget = configuredReportsDatabase(
    readFileSync(resolve(WORKER_ROOT, "wrangler.toml"), "utf8"),
  );
  assert.equal(actualTarget.binding, "REPORTS_DB");
  assert.ok(actualTarget.databaseName);
  assert.ok(actualTarget.databaseId);
});


test("deduplication migration adds the hash column and partial unique index", () => {
  const migration = readFileSync(
    resolve(WORKER_ROOT, "migrations/0003_add_payload_deduplication.sql"),
    "utf8",
  );
  assert.match(migration, /ADD COLUMN payload_hash TEXT/);
  assert.match(migration, /CREATE UNIQUE INDEX IF NOT EXISTS bug_reports_payload_hash_unique_idx/);
  assert.match(migration, /WHERE payload_hash IS NOT NULL/);
});


test("deployment verifier unwraps Wrangler JSON without executing mutations", () => {
  const payload = [{ success: true, results: validSchemaRows() }];
  const parsed = parseWranglerJson(`wrangler informational banner\n${JSON.stringify(payload)}`);
  assert.deepEqual(unwrapD1Rows(parsed), validSchemaRows());
});
