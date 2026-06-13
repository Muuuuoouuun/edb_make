(function attachPublishSummaryHelpers(root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = helpers;
  }
  root.EDB_PUBLISH_SUMMARY = helpers;
})(typeof globalThis !== "undefined" ? globalThis : window, function createPublishSummaryHelpers() {
  function positiveNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, number) : 0;
  }

  function formatRecordCountLabel(summary) {
    const explicit = String(summary?.recordCountLabel || summary?.record_count_label || "").trim();
    if (explicit) return explicit;
    const supplemental = positiveNumber(summary?.supplementalItemCount ?? summary?.supplemental_item_count);
    const coreRaw = Number(summary?.coreProblemCount ?? summary?.core_problem_count);
    if (supplemental > 0 && Number.isFinite(coreRaw)) {
      return `${Math.max(0, coreRaw)}문항 + 자료 ${supplemental}`;
    }
    const recordCount = positiveNumber(
      summary?.recordCount
      ?? summary?.record_count
      ?? summary?.recordCountActual
      ?? summary?.record_count_actual
    );
    return `${recordCount}개 자료`;
  }

  function normalizePublishSummary(raw, session = null) {
    if (!raw || typeof raw !== "object") return null;
    const recordCount = positiveNumber(raw.recordCount ?? raw.record_count ?? raw.recordCountActual ?? raw.record_count_actual);
    const recordCountActual = positiveNumber(raw.recordCountActual ?? raw.record_count_actual ?? recordCount);
    const coreProblemCount = positiveNumber(raw.coreProblemCount ?? raw.core_problem_count);
    const supplementalItemCount = positiveNumber(raw.supplementalItemCount ?? raw.supplemental_item_count);
    const pageCountHint = positiveNumber(raw.pageCountHint ?? raw.page_count_hint);
    const outerSize = positiveNumber(raw.outerSize ?? raw.outer_size);
    const edbFileName = String(raw.edbFileName || raw.edb_file_name || "").trim();
    const edbPath = String(raw.edbPath || raw.edb_path || "").trim();
    const outputDir = String(raw.outputDir || raw.output_dir || session?.output_dir || session?.outputDir || "").trim();
    const edbFileUri = String(raw.edbFileUri || raw.edb_file_uri || session?.edb_file_uri || session?.edbFileUri || "").trim();
    const classinReview = raw.classinReview || raw.classin_review || session?.classinReview || session?.classin_review || {};
    const classinReviewStatus = String(
      raw.classinReviewStatus
      || raw.classin_review_status
      || classinReview.status
      || ""
    ).trim();
    const classinReviewStatusLabel = String(
      raw.classinReviewStatusLabel
      || raw.classin_review_status_label
      || classinReview.statusLabel
      || classinReview.status_label
      || (classinReviewStatus === "passed" ? "ClassIn 확인 완료" : "")
    ).trim();
    const classinHandoffUri = String(
      raw.classinHandoffUri
      || raw.classin_handoff_uri
      || session?.classin_handoff_uri
      || session?.classinHandoffUri
      || ""
    ).trim();
    const classinHandoffMarkdownUri = String(
      raw.classinHandoffMarkdownUri
      || raw.classin_handoff_markdown_uri
      || session?.classin_handoff_markdown_uri
      || session?.classinHandoffMarkdownUri
      || ""
    ).trim();
    const classinPreflight = raw.classinPreflight || raw.classin_preflight || session?.classinPreflight || session?.classin_preflight || {};
    const classinPreflightStatus = String(
      raw.classinPreflightStatus
      || raw.classin_preflight_status
      || classinPreflight.status
      || ""
    ).trim();
    const classinPreflightIssueCount = positiveNumber(
      raw.classinPreflightIssueCount
      ?? raw.classin_preflight_issue_count
      ?? classinPreflight.issueCount
      ?? classinPreflight.issue_count
    );
    const rawClassinPreflightPassed = raw.classinPreflightPassed ?? raw.classin_preflight_passed ?? classinPreflight.passed;
    const classinPreflightPassed = rawClassinPreflightPassed === undefined
      ? classinPreflightStatus === "passed"
      : rawClassinPreflightPassed !== false;
    const classinPreflightStatusLabel = String(
      raw.classinPreflightStatusLabel
      || raw.classin_preflight_status_label
      || (classinPreflightStatus
        ? (classinPreflightPassed ? "ClassIn 사전점검 OK" : `ClassIn 사전점검 주의 ${classinPreflightIssueCount}`)
        : "")
    ).trim();
    const edbFileExists = raw.edbFileExists ?? raw.edb_file_exists;
    const outputDirExists = raw.outputDirExists ?? raw.output_dir_exists;
    if (!edbFileName && !edbPath && !edbFileUri) return null;
    const summary = {
      validated: raw.validated !== false,
      statusLabel: String(raw.statusLabel || raw.status_label || "제작 완료"),
      edbFileName: edbFileName || (edbPath ? edbPath.split("/").pop() : "classin.edb"),
      edbPath,
      edbFileUri,
      outputDir,
      classinReview,
      classinReviewStatus,
      classinReviewStatusLabel,
      classinReviewPassed: (raw.classinReviewPassed ?? raw.classin_review_passed) === undefined
        ? classinReviewStatus === "passed"
        : (raw.classinReviewPassed ?? raw.classin_review_passed) !== false,
      classinHandoffUri,
      classinHandoffMarkdownUri,
      classinPreflight,
      classinPreflightStatus,
      classinPreflightStatusLabel,
      classinPreflightPassed,
      classinPreflightIssueCount,
      edbFileExists: edbFileExists === undefined ? true : edbFileExists !== false,
      outputDirExists: outputDirExists === undefined ? Boolean(outputDir) : outputDirExists !== false,
      recordCount,
      recordCountActual,
      coreProblemCount,
      supplementalItemCount,
      pageCountHint,
      outerSize,
      publishedAt: String(raw.publishedAt || raw.published_at || "").trim(),
    };
    summary.canDownload = Boolean(summary.edbFileUri) && summary.edbFileExists !== false;
    summary.canOpenEdbFile = Boolean(summary.edbPath) && summary.edbFileExists !== false;
    summary.canOpenOutputDir = Boolean(summary.outputDir) && summary.outputDirExists !== false;
    summary.canOpenClassinHandoff = Boolean(summary.classinHandoffMarkdownUri || summary.classinHandoffUri);
    summary.canMarkClassinReviewComplete = summary.canOpenEdbFile && !summary.classinReviewPassed;
    summary.recordCountLabel = formatRecordCountLabel({ ...raw, ...summary });
    return summary;
  }

  function formatPublishTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatPublishHistoryMeta(summary) {
    const recordLabel = formatRecordCountLabel(summary);
    const timeLabel = formatPublishTime(summary?.publishedAt || summary?.published_at);
    return [timeLabel, recordLabel].filter(Boolean).join(" · ") || recordLabel;
  }

  return {
    formatPublishHistoryMeta,
    formatRecordCountLabel,
    formatPublishTime,
    normalizePublishSummary,
  };
});
