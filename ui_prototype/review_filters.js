(function attachReviewFilterHelpers(root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = helpers;
  }
  root.EDB_REVIEW_FILTERS = helpers;
})(typeof globalThis !== "undefined" ? globalThis : window, function createReviewFilterHelpers() {
  function riskFlagsFor(value) {
    if (!value || typeof value !== "object") return [];
    const rawFlags = value.riskFlags || value.risk_flags || value.metadata?.risk_flags || [];
    return Array.isArray(rawFlags)
      ? rawFlags.map(flag => String(flag || "").trim()).filter(Boolean)
      : [];
  }

  function hasRiskFlag(value, flag) {
    const target = String(flag || "").trim();
    if (!target) return false;
    return riskFlagsFor(value).includes(target);
  }

  function isSupplementalProblem(problem) {
    if (!problem || typeof problem !== "object") return false;
    if (hasRiskFlag(problem, "marker_document_continuation")) return true;
    if (problem.metadata?.marker_document_continuation) return true;
    const id = String(problem.id || problem.problem_id || "");
    return id.endsWith("-continuation");
  }

  function deriveProblemStatus(problem) {
    const rawStatus = String(problem?.reviewStatus || problem?.review_status || "").trim();
    if (rawStatus === "normal" || rawStatus === "check_needed" || rawStatus === "failed") {
      return rawStatus;
    }
    if (!problem || typeof problem !== "object") return "normal";
    const bbox = problem.bbox || {};
    if (!bbox.width || !bbox.height || problem.parseFailed || problem.parse_failed) return "failed";
    return riskFlagsFor(problem).length ? "check_needed" : "normal";
  }

  function problemMatchesReviewFilter(problem, filter) {
    const normalizedFilter = String(filter || "all").trim() || "all";
    if (normalizedFilter === "all") return true;
    if (normalizedFilter === "supplemental") return isSupplementalProblem(problem);
    return deriveProblemStatus(problem) === normalizedFilter;
  }

  function countReviewFilters(problems) {
    const counts = { all: 0, normal: 0, check_needed: 0, failed: 0, supplemental: 0 };
    if (!Array.isArray(problems)) return counts;
    problems.forEach(problem => {
      const status = deriveProblemStatus(problem);
      counts.all += 1;
      counts[status] = (counts[status] || 0) + 1;
      if (isSupplementalProblem(problem)) counts.supplemental += 1;
    });
    return counts;
  }

  return {
    countReviewFilters,
    deriveProblemStatus,
    hasRiskFlag,
    isSupplementalProblem,
    problemMatchesReviewFilter,
    riskFlagsFor,
  };
});
