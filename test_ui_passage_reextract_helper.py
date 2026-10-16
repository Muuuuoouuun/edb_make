from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiPassageReextractHelper(unittest.TestCase):
    def test_reextract_replaces_auto_passages_and_restores_manual_child(self) -> None:
        script = textwrap.dedent(
            r"""
            const assert = require('assert');
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('function sessionReusableSourcePaths');
            const end = source.indexOf('function reviewScopeForNewSession', start);
            assert(start >= 0 && end > start);

            const sandbox = {
              cloneSession(value) {
                return value == null ? value : JSON.parse(JSON.stringify(value));
              },
              listUnique(values) {
                return Array.from(new Set(values));
              },
              isPassageFragmentProblem(problem) {
                const role = problem?.passageRole
                  || problem?.passage_role
                  || problem?.metadata?.passageRole
                  || problem?.metadata?.passage_role;
                return role === 'passage_fragment';
              },
              passageGroupIdFor(problem) {
                return String(
                  problem?.passageGroupId
                  || problem?.passage_group_id
                  || problem?.metadata?.passageGroupId
                  || problem?.metadata?.passage_group_id
                  || ''
                );
              },
              makeUniqueId(value, used) {
                const base = String(value || 'item');
                let next = base;
                let suffix = 2;
                while (used.has(next)) next = `${base}-${suffix++}`;
                used.add(next);
                return next;
              },
              applyProblemCounts(session, problems) {
                session.problem_count = problems.length;
              },
            };
            vm.runInNewContext(source.slice(start, end), sandbox);

            const base = {
              session_name: '국어',
              input_files: ['source.pdf'],
              problems: [
                {
                  id: 'old-passage',
                  title: '옛 지문',
                  passageRole: 'passage_fragment',
                  passageGroupId: 'group-1',
                  sourcePageId: 'page-1',
                  originalImagePath: 'old.png',
                },
                {
                  id: 'q1',
                  title: '1.',
                  passageRole: 'child_question',
                  passageGroupId: 'group-1',
                  sourcePageId: 'page-1',
                  originalImagePath: 'q1.png',
                  processingStep: 's3',
                  crop: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
                  placementScaleRatio: 1.7,
                },
                {
                  id: 'q2',
                  title: '2.',
                  passageRole: 'passage_fragment',
                  passageGroupId: 'group-1',
                  classificationSource: 'manual',
                  supplementalItem: true,
                  sourcePageId: 'page-1',
                  originalImagePath: 'q2.png',
                  processingStep: 's2',
                  crop: { x: 0.2, y: 0.3, width: 0.4, height: 0.5 },
                },
                {
                  id: 'manual-passage',
                  title: '직접 만든 지문',
                  passageRole: 'passage_fragment',
                  passageGroupId: 'manual-passage-manual-passage',
                  classificationSource: 'manual',
                  supplementalItem: true,
                  sourcePageId: 'page-1',
                  originalImagePath: 'manual.png',
                },
              ],
              pages: [{
                id: 'page-1',
                sourceImagePath: 'page-1.png',
                problemIds: ['old-passage', 'q1', 'q2', 'manual-passage'],
                problem_ids: ['old-passage', 'q1', 'q2', 'manual-passage'],
              }],
            };
            const incoming = {
              problems: [{
                id: 'new-passage',
                title: '새 지문',
                passageRole: 'passage_fragment',
                passageGroupId: 'group-1',
                sourcePageId: 'page-1',
                originalImagePath: 'new.png',
              }],
              pages: [{
                id: 'page-1',
                sourceImagePath: 'page-1.png',
                problemIds: ['new-passage'],
              }],
            };

            const merged = sandbox.mergeReextractedSharedPassages(base, incoming, '국어');
            assert.deepStrictEqual(
              Array.from(merged.problems, problem => problem.id),
              ['new-passage', 'q1', 'q2', 'manual-passage']
            );
            assert.strictEqual(merged.problems[0].originalImagePath, 'new.png');
            assert(!merged.problems.some(problem => problem.id === 'old-passage'));
            assert(!merged.problems.some(problem => problem.id.includes('-2')));

            const q1 = merged.problems.find(problem => problem.id === 'q1');
            assert.strictEqual(q1.originalImagePath, 'q1.png');
            assert.strictEqual(q1.processingStep, 's3');
            assert.strictEqual(q1.placementScaleRatio, 1.7);
            assert.deepStrictEqual(q1.crop, { x: 0.1, y: 0.2, width: 0.3, height: 0.4 });

            const q2 = merged.problems.find(problem => problem.id === 'q2');
            assert.strictEqual(q2.passageRole, 'child_question');
            assert.strictEqual(q2.supplementalItem, false);
            assert.strictEqual(q2.originalImagePath, 'q2.png');
            assert.strictEqual(q2.processingStep, 's2');
            assert.deepStrictEqual(q2.crop, { x: 0.2, y: 0.3, width: 0.4, height: 0.5 });

            const page = merged.pages.find(candidate => candidate.id === 'page-1');
            assert.deepStrictEqual(
              Array.from(page.problemIds),
              ['new-passage', 'q1', 'q2', 'manual-passage']
            );
            assert.deepStrictEqual(Array.from(page.problem_ids), Array.from(page.problemIds));

            const mergedAgain = sandbox.mergeReextractedSharedPassages(merged, incoming, '국어');
            assert.deepStrictEqual(
              Array.from(mergedAgain.problems, problem => problem.id),
              ['new-passage', 'q1', 'q2', 'manual-passage']
            );
            assert.strictEqual(
              mergedAgain.problems.filter(sandbox.isPassageFragmentProblem).length,
              2
            );
            """
        )
        run_node(script)

    def test_reextract_source_paths_fall_back_to_page_images(self) -> None:
        script = textwrap.dedent(
            r"""
            const assert = require('assert');
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('function sessionReusableSourcePaths');
            const end = source.indexOf('function problemClassificationSource', start);
            const sandbox = {
              listUnique(values) {
                return Array.from(new Set(values));
              },
            };
            vm.runInNewContext(source.slice(start, end), sandbox);
            const paths = sandbox.sessionReusableSourcePaths({
              pages: [
                { sourceImagePath: 'page-1.png' },
                { sourceImageUri: 'page-1.png' },
                { source_image_uri: 'page-2.png' },
              ],
            });
            assert.deepStrictEqual(Array.from(paths), ['page-1.png', 'page-2.png']);
            """
        )
        run_node(script)


if __name__ == "__main__":
    unittest.main()
