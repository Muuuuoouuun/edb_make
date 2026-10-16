"""Execute the integrated recognition transaction rather than matching UI text."""
from __future__ import annotations

import unittest

from test_ui_placement_details import run_node


SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
const start = source.indexOf('  const applyRecognitionResult = useCallback(');
const end = source.indexOf('\n  useEffect(() => {', start);
assert.ok(start >= 0 && end > start);
function transaction(overrides = {}) {
  const events = [], queued = [{key:'picked'}, {key:'later'}];
  const sandbox = {
    useCallback: fn => fn,
    recognitionReview: {kind:'queue-recognition', queueGeneration:1, fileKeys:['picked'],
      incomingSession:{problems:[{id:'new'}]}},
    recognitionApplyInFlightRef:{current:false}, confirmingRecognition:false,
    session:{problems:[{id:'existing'}]}, usingMock:false,
    items:[], fileName:'lesson', boardColumns:1, layoutGapMode:'classin-grid',
    UNDO_HISTORY_LIMIT:10,
    materializeSessionForItems: s => structuredClone(s),
    cloneSession: s => structuredClone(s),
    mergeSessions: (a,b) => ({problems:[...a.problems,...b.problems]}),
    postRestore: async s => {events.push(['persist',s]); return s;},
    setConfirmingRecognition: value => events.push(['busy',value]),
    setRecognitionReview: value => {sandbox.recognitionReview=value;},
    setHistoryStack: fn => {sandbox.history=fn(sandbox.history || []);},
    appendBoundedHistory: (history,s) => [...history,s],
    applySession: s => events.push(['apply',s]),
    adoptMutatedSession: s => events.push(['adopt',s]),
    reviewFocusForNewSession: () => ({source:'queue-recognition'}),
    setReviewFocus() {}, refreshSessionHistory() {},
    queueRequestIsCurrent: () => true,
    setView: view => events.push(['view',view]),
    requestViewChange: view => events.push(['requestView',view]),
    setPendingFilesTracked: fn => {sandbox.queue=fn(sandbox.queue);},
    fileQueueKey: file => file.key,
    summarizeRecognitionSession: () => ({problemLabel:'1 item'}),
    sessionReviewSummary: () => ({actionableNeedsReviewCount:1}),
    showToast: message => events.push(['toast',message]),
    showSimpleErrorToast: error => events.push(['error',error.message]),
    isPassageFragmentProblem: p => p.kind === 'passage',
    isManualIndependentPassage: () => false,
    mergeRetryCandidateIntoCurrent: (current,incoming) => ({problems:[...current.problems,...incoming.problems]}),
    queue:queued,
    ...overrides,
  };
  vm.createContext(sandbox);
  vm.runInContext(source.slice(start,end) + '\nglobalThis.applyResult=applyRecognitionResult;',sandbox);
  return {sandbox,events};
}
"""


class TestRecognitionAutoApply(unittest.TestCase):
    def test_persists_before_applying_and_preserves_existing_items_and_later_queue(self):
        run_node(SETUP + r"""
        (async () => {
          const {sandbox:s,events} = transaction();
          await s.applyResult();
          assert.deepEqual(events.filter(e=>['persist','apply'].includes(e[0])).map(e=>e[0]),['persist','apply']);
          assert.deepEqual(events.find(e=>e[0]==='apply')[1].problems.map(p=>p.id),['existing','new']);
          assert.deepEqual(s.queue.map(f=>f.key),['later']);
          assert.equal(s.history.length,1);
          assert.equal(s.recognitionReview,null);
          assert.equal(s.recognitionApplyInFlightRef.current,false);
          assert.ok(events.some(e=>e[0]==='requestView' && e[1]==='board'));
          assert.ok(events.filter(e=>['view','requestView'].includes(e[0])).every(e=>e[1]==='board'),
            'recognition risks must not force an intermediate review screen');
        })().catch(e=>{console.error(e);process.exitCode=1;});
        """)

    def test_reentrant_apply_only_persists_once(self):
        run_node(SETUP + r"""
        (async () => {
          let finish, calls=0;
          const gate=new Promise(resolve=>{finish=resolve;});
          const {sandbox:s} = transaction({postRestore:async value=>{calls++;await gate;return value;}});
          const first=s.applyResult();
          await s.applyResult();
          assert.equal(calls,1);
          assert.equal(s.recognitionApplyInFlightRef.current,true);
          finish(); await first;
          assert.equal(s.recognitionApplyInFlightRef.current,false);
        })().catch(e=>{console.error(e);process.exitCode=1;});
        """)

    def test_failure_preserves_queue_and_clears_retrying_candidate(self):
        run_node(SETUP + r"""
        (async () => {
          const {sandbox:s,events}=transaction({postRestore:async()=>{throw new Error('disk unavailable');}});
          await s.applyResult();
          assert.deepEqual(s.queue.map(f=>f.key),['picked','later']);
          assert.equal(s.recognitionReview,null);
          assert.equal(s.recognitionApplyInFlightRef.current,false);
          assert.equal(events.filter(e=>e[0]==='apply').length,0);
          assert.equal(events.filter(e=>e[0]==='error').length,1);
        })().catch(e=>{console.error(e);process.exitCode=1;});
        """)

    def test_empty_passage_result_does_not_loop(self):
        run_node(SETUP + r"""
        (async () => {
          const {sandbox:s,events}=transaction({recognitionReview:{kind:'session-passage-reextract',incomingSession:{problems:[]}}});
          await s.applyResult();
          await s.applyResult();
          assert.equal(s.recognitionReview,null);
          assert.equal(events.filter(e=>e[0]==='toast').length,1);
          assert.equal(events.filter(e=>e[0]==='persist').length,0);
        })().catch(e=>{console.error(e);process.exitCode=1;});
        """)

    def test_stale_queue_result_is_not_persisted(self):
        run_node(SETUP + r"""
        (async () => {
          const {sandbox:s,events}=transaction({queueRequestIsCurrent:()=>false});
          await s.applyResult();
          assert.equal(s.recognitionReview,null);
          assert.equal(events.filter(e=>e[0]==='persist').length,0);
          assert.equal(s.queue.length,2);
        })().catch(e=>{console.error(e);process.exitCode=1;});
        """)
