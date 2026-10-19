from __future__ import annotations

import unittest
from test_ui_placement_details import NODE_SETUP, run_node

PUBLISH_SETUP = NODE_SETUP + r"""
function publishSandbox(response) {
  const events = { adopted: [], errors: [], requests: 0, downloads: 0, captures: 0 };
  const session = { session_name: '미저장 수업', problems: [{ id: 'p1' }, { id: 'p2' }] };
  const sandbox = {
    hasRunningQueueRecognition:false, confirmingRecognition:false, queueRegistrationInFlightRef:{current:false}, pendingFiles:[],
    session, items: session.problems, fileName: session.session_name, boardColumns: 1, layoutGapMode: 'classin-grid',
    BOARD_COLUMN_MIN: 1, DEFAULT_SLOT_HEIGHT_PAGES: 1.2, publishInFlightRef: { current: false }, operationRecovery: null,
    publishRequestDraft: { sessionIds: new Set(['p1', 'p2']), order: ['p1', 'p2'], excluded: [], itemsForPublish: session.problems, sessionForPublish: session, placements: [] },
    normalizeBoardColumns: value => value, sessionReviewSummary: () => ({}),
    findPassageGroupSourceReuse: () => [], findSourceProblemOverlaps: () => [], findBoardPlacementOverlaps: () => [],
    publishReviewWarningMessage: () => null, clearOperationRecovery() {}, showToast() {},
    setPublishBusy: value => { events.busy = value; }, setLoading: value => { events.loading = value; },
    fetch: async () => { events.requests++; return { ok: true, status: 200 }; },
    withExpectedSessionRevision: value => value, edbFileNameFromSessionName: value => value,
    readJsonResponse: async () => response, sessionResponseRevisionIsStale: () => false,
    captureSessionRevision: () => { events.captures++; }, normalizeLayoutGapMode: value => value,
    mapProblemToItem: value => value, reflowItemsForBoardOrder: value => value,
    setSession: value => { events.adopted.push(value); sandbox.session = value; }, setItems: value => { sandbox.items = value; },
    setActiveId() {}, setLayoutGapModeState() {}, refreshSessionHistory() {},
    normalizePublishSummary: () => ({canDownload:true}), setPublished: value => { events.published = value; },
    setDownloadBusy() {}, downloadPublishSummary: async () => { events.downloads++; return {ok:true}; },
    activateOperationRecovery: (error, message, detail, options) => {events.errors.push({error,options});},
    isNetworkRequestError: () => false,
  };
  vm.createContext(sandbox);
  // Load the validator when present so the first run demonstrates the old failure.
  if(source.includes('function assertPublishResponseSession('))
    vm.runInContext(section('function assertPublishResponseSession(', 'function assertPlacementSaveSession('), sandbox);
  vm.runInContext(section('  const onPublish = async () => {', '  const handlePublishDownload =') + '\nglobalThis.publish = onPublish;', sandbox);
  return { sandbox, events, session };
}
"""


class TestPublishResponseValidation(unittest.TestCase):
    def test_incomplete_success_responses_preserve_draft_and_release_busy_state(self):
        run_node(PUBLISH_SETUP + r"""
        (async () => {
          for(const bad of [undefined, null, [], {}, {problems:[]}, {problems:[null]},
            {problems:[{id:'p1'}]}, {problems:[{id:'p1'},{id:'p1'}]},
            {problems:[{id:'p1'},{id:'wrong'}]}, {problems:[{id:'p1'},{id:'p2'},{id:'extra'}]}]) {
            const {sandbox,events,session}=publishSandbox({ok:true,session:bad});
            const before=JSON.stringify(sandbox.items);
            await sandbox.publish();
            assert.equal(events.adopted.length,0, 'invalid response must not replace draft: '+JSON.stringify(bad));
            assert.equal(sandbox.session,session);
            assert.equal(JSON.stringify(sandbox.items),before);
            assert.equal(events.captures,0);
            assert.equal(events.downloads,0);
            assert.equal(events.errors.length,1);
            assert.equal(events.errors[0].error.code,'invalid_publish_response');
            assert.equal(events.errors[0].options.localDraft,session);
            assert.equal(sandbox.publishInFlightRef.current,false);
            assert.equal(events.busy,false);
            assert.equal(events.loading,null);
          }
        })().catch(error => {console.error(error);process.exitCode=1});
        """)

    def test_complete_response_is_adopted_and_downloaded(self):
        run_node(PUBLISH_SETUP + r"""
        (async () => {
          const returned={problems:[{id:'p1'},{id:'p2'}]};
          const {sandbox,events}=publishSandbox({ok:true,session:returned});
          await sandbox.publish();
          assert.equal(sandbox.session,returned);
          assert.equal(events.adopted.length,1);
          assert.equal(events.downloads,1);
          assert.equal(events.captures,1);
          assert.equal(events.errors.length,0);
          assert.equal(events.published,true);
          assert.equal(sandbox.publishInFlightRef.current,false);
        })().catch(error => {console.error(error);process.exitCode=1});
        """)
