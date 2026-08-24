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

  function passageRoleFor(problem) {
    if (!problem || typeof problem !== "object") return "";
    return String(
      problem.passageRole
      || problem.passage_role
      || problem.metadata?.passageRole
      || problem.metadata?.passage_role
      || ""
    ).trim();
  }

  function sessionReviewMode(session) {
    const contentTarget = String(
      session?.contentTarget
      || session?.content_target
      || ""
    ).trim().toLowerCase().replace(/_/g, "-");
    if (contentTarget === "shared-passages") return "shared-passages";

    const inputIntent = String(
      session?.inputIntent
      || session?.input_intent
      || ""
    ).trim().toLowerCase().replace(/_/g, "-");
    if (inputIntent === "page-as-is") return "page-as-is";

    const problems = Array.isArray(session?.problems) ? session.problems : [];
    if (problems.length && problems.every(problem => passageRoleFor(problem) === "passage_fragment")) {
      return "shared-passages";
    }
    return "problems";
  }

  function nonnegativeCount(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, number) : 0;
  }

  function reviewCountParts(counts) {
    const supplemental = nonnegativeCount(counts?.supplemental ?? counts?.supplementalItems);
    const core = nonnegativeCount(counts?.core ?? counts?.problems ?? counts?.total);
    const explicitTotal = Number(counts?.total ?? counts?.all);
    const total = Number.isFinite(explicitTotal)
      ? Math.max(0, explicitTotal)
      : core + supplemental;
    return { core, supplemental, total };
  }

  function formatReviewModeCount(mode, counts, options = {}) {
    const normalizedMode = String(mode || "problems").trim();
    const parts = reviewCountParts(counts);
    if (normalizedMode === "shared-passages") {
      return `${options.compact ? "지문" : "공통 지문"} ${parts.total}개`;
    }
    if (normalizedMode === "page-as-is") {
      const explicitPageCount = Number(options.pageCount);
      const pageCount = Number.isFinite(explicitPageCount) ? Math.max(0, explicitPageCount) : parts.total;
      return options.compact ? "페이지 원본" : `${pageCount}페이지`;
    }
    if (parts.supplemental > 0) return `${parts.core}문항 + 자료 ${parts.supplemental}`;
    return `${parts.core}문항`;
  }

  function reviewModeCopy(session, counts, options = {}) {
    const mode = sessionReviewMode(session);
    const pageCount = nonnegativeCount(options.pageCount ?? session?.pages?.length);
    const countLabel = formatReviewModeCount(mode, counts, { pageCount });
    const compactCountLabel = formatReviewModeCount(mode, counts, { pageCount, compact: true });
    if (mode === "shared-passages") {
      return {
        mode,
        title: "지문 검수",
        subtitle: "공통 지문 영역",
        countLabel,
        headerCountLabel: `${pageCount}페이지 · ${compactCountLabel}`,
      };
    }
    if (mode === "page-as-is") {
      return {
        mode,
        title: "페이지 검수",
        subtitle: "원본 보존",
        countLabel,
        headerCountLabel: `${pageCount}페이지 원본`,
      };
    }
    return {
      mode,
      title: "문항 검수",
      subtitle: "검출 영역",
      countLabel,
      headerCountLabel: `${pageCount}페이지 · ${compactCountLabel}`,
    };
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

  function problemMatchesReviewFilter(problem, filter, options = {}) {
    const normalizedFilter = String(filter || "all").trim() || "all";
    if (normalizedFilter === "all") return true;
    if (normalizedFilter === "supplemental") return isSupplementalProblem(problem);
    if (normalizedFilter === "passage") return isPassageProblem(problem);
    if (normalizedFilter === "passage-review") return isPassageReviewProblem(problem, options);
    return deriveProblemStatus(problem) === normalizedFilter;
  }

  function countReviewFilters(problems) {
    const counts = { all: 0, normal: 0, check_needed: 0, failed: 0, supplemental: 0, passage: 0, passageGroups: 0 };
    if (!Array.isArray(problems)) return counts;
    const passageGroups = new Set();
    problems.forEach(problem => {
      const status = deriveProblemStatus(problem);
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
    formatReviewModeCount,
    reviewModeCopy,
    riskFlagsFor,
    sessionReviewMode,
  };
});
