import assert from "node:assert/strict";
import test from "node:test";

import worker, { reportId, validateReport } from "../src/index.js";


function validPayload() {
  return {
    schemaVersion: 1,
    category: "bug",
    description: "설정 화면에서 저장 버튼이 작동하지 않습니다.",
    app: {
      id: "ClassInEDBMVP",
      version: "0.1.0",
      platform: "macos",
    },
    context: {
      view: "board",
      settingsTab: "board",
      itemCount: 3,
    },
    diagnostics: {
      system: "Darwin",
    },
  };
}


class FakeStatement {
  constructor(database, sql) {
    this.database = database;
    this.sql = sql;
    this.values = [];
  }

  bind(...values) {
    this.values = values;
    return this;
  }

  async run() {
    this.database.rows.push({ sql: this.sql, values: this.values });
    return { success: true };
  }
}


class FakeD1 {
  constructor() {
    this.rows = [];
  }

  prepare(sql) {
    return new FakeStatement(this, sql);
  }
}


test("report ids are recognizable and unique", () => {
  const first = reportId(new Date("2026-07-27T00:00:00Z"));
  const second = reportId(new Date("2026-07-27T00:00:00Z"));
  assert.match(first, /^EDB-20260727-[0-9A-F]{10}$/);
  assert.notEqual(first, second);
});


test("validation accepts the EDB schema and rejects unknown apps", () => {
  assert.equal(validateReport(validPayload()), "");
  const unknown = validPayload();
  unknown.app.id = "OtherApp";
  assert.equal(validateReport(unknown), "unknown_app");
});


test("health endpoint is public and does not touch storage", async () => {
  const response = await worker.fetch(
    new Request("https://reports.classin.cloud/health"),
    {},
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    ok: true,
    service: "classin-edb-reports",
  });
});


test("valid reports are inserted and receive a receipt", async () => {
  const database = new FakeD1();
  const response = await worker.fetch(
    new Request("https://reports.classin.cloud/v1/edb-reports", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(validPayload()),
    }),
    { REPORTS_DB: database },
  );
  assert.equal(response.status, 201);
  const receipt = await response.json();
  assert.equal(receipt.ok, true);
  assert.match(receipt.reportId, /^EDB-\d{8}-[0-9A-F]{10}$/);
  assert.equal(database.rows.length, 1);
  assert.equal(database.rows[0].values[6], validPayload().description);
});


test("collector rejects short descriptions and oversized payloads", async () => {
  const short = validPayload();
  short.description = "짧음";
  const shortResponse = await worker.fetch(
    new Request("https://reports.classin.cloud/v1/edb-reports", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(short),
    }),
    { REPORTS_DB: new FakeD1() },
  );
  assert.equal(shortResponse.status, 400);

  const oversizedResponse = await worker.fetch(
    new Request("https://reports.classin.cloud/v1/edb-reports", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "content-length": String(70 * 1024),
      },
      body: "{}",
    }),
    { REPORTS_DB: new FakeD1() },
  );
  assert.equal(oversizedResponse.status, 413);
});
