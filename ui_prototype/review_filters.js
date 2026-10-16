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
    const role = String(problem.passageRole || problem.passage_role || problem.metadata?.passageRole || problem.metadata?.passage_role || "").trim();
    if (role === "passage_fragment") return true;
    if (problem.supplementalItem || problem.supplemental_item || problem.metadata?.supplementalItem || problem.metadata?.supplemental_item) return true;
    if (hasRiskFlag(problem, "marker_document_continuation")) return true;
    if (problem.metadata?.marker_document_continuation) return true;
    const id = String(problem.id || problem.problem_id || "");
    return id.endsWith("-continuation");
  }

  function passageGroupIdFor(problem) {
    if (!problem || typeof problem !== "object") return "";
    return String(
      problem.passageGroupId
      || problem.passage_group_id
      || problem.metadata?.passageGroupId
      || problem.metadata?.passage_group_id
      || ""
    ).trim();
  }

  function isPassageProblem(problem) {
    return Boolean(passageGroupIdFor(problem));
  }

  function problemIdFor(problem) {
    if (!problem || typeof problem !== "object") return "";
    return String(problem.id || problem.problem_id || "").trim();
  }

  function reviewProblemIdSet(options) {
    const rawIds = options?.passageReviewProblemIds || options?.passage_review_problem_ids || [];
    if (rawIds instanceof Set) return rawIds;
    return new Set(
      Array.isArray(rawIds)
        ? rawIds.map(id => String(id || "").trim()).filter(Boolean)
        : []
    );
  }

  function isPassageReviewProblem(problem, options = {}) {
    const id = problemIdFor(problem);
    return Boolean(id && reviewProblemIdSet(options).has(id));
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

  // A problem whose own status is normal still needs review when its page
  // carries an unresolved warning (options.unresolvedProblemIds), matching
  // the count shown on the 검수 tab badge and in the EDB publish warning.
  function reviewStatusFor(problem, options = {}) {
    const status = deriveProblemStatus(problem);
    if (status !== "normal") return status;
    const rawIds = options?.unresolvedProblemIds;
    if (!rawIds) return status;
    const idSet = rawIds instanceof Set ? rawIds : new Set(Array.isArray(rawIds) ? rawIds.map(String) : []);
    const id = problemIdFor(problem);
    return id && idSet.has(id) ? "check_needed" : status;
  }

  function problemMatchesReviewFilter(problem, filter, options = {}) {
    const normalizedFilter = String(filter || "all").trim() || "all";
    if (normalizedFilter === "all") return true;
    if (normalizedFilter === "supplemental") return isSupplementalProblem(problem);
    if (normalizedFilter === "passage") return isPassageProblem(problem);
    if (normalizedFilter === "passage-review") return isPassageReviewProblem(problem, options);
    return reviewStatusFor(problem, options) === normalizedFilter;
  }

  function countReviewFilters(problems, options = {}) {
    const counts = { all: 0, normal: 0, check_needed: 0, failed: 0, supplemental: 0, passage: 0, passageGroups: 0 };
    if (!Array.isArray(problems)) return counts;
    const passageGroups = new Set();
    problems.forEach(problem => {
      const status = reviewStatusFor(problem, options);
      counts.all += 1;
      counts[status] = (counts[status] || 0) + 1;
      if (isSupplementalProblem(problem)) counts.supplemental += 1;
      const passageGroupId = passageGroupIdFor(problem);
      if (passageGroupId) {
        counts.passage += 1;
        passageGroups.add(passageGroupId);
      }
    });
    counts.passageGroups = passageGroups.size;
    return counts;
  }

  return {
    countReviewFilters,
    deriveProblemStatus,
    hasRiskFlag,
    isPassageProblem,
    isPassageReviewProblem,
    isSupplementalProblem,
    passageGroupIdFor,
    problemIdFor,
    problemMatchesReviewFilter,
    reviewStatusFor,
    riskFlagsFor,
  };
});
