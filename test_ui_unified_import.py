"""Automatic import scheduling and frozen-batch cancellation boundaries."""
from __future__ import annotations

import unittest
from test_ui_home_flows import NODE_SETUP, run_node


SETUP = NODE_SETUP + r"""
const automatic = section('  useEffect(() => {\n    if (!initialSessionLoaded || loading || mutating || hasRunningBackgroundJobs', '  const setStep =');
function scheduler() {
  const calls = [];
  const sandbox = {
    initialSessionLoaded:true, loading:null, mutating:false, hasRunningBackgroundJobs:false,
    recognitionReview:null, confirmingRecognition:false, hasPendingSessionConflict:false,
    operationRecovery:null, reviewEditorActive:false, splitMode:'recognize',
    pendingFiles:[{name:'a.pdf'},{name:'b.pdf'}], autoProcessKeysRef:{current:new Set(['a.pdf','b.pdf'])},
    fileQueueKey:file=>file.name, normalizeSplitMode:value=>value,
    processQueuedFiles:(mode,target,keys)=>calls.push({mode,keys:Array.from(keys)}),
    useEffect:effect=>effect(),
  };
  vm.createContext(sandbox);
  return {sandbox,calls,tick:()=>vm.runInContext(automatic,sandbox)};
}
"""


class TestUnifiedImport(unittest.TestCase):
    def test_one_automatic_request_per_frozen_batch_and_append_runs_next(self):
        run_node(SETUP + r"""
        const {sandbox:s,calls,tick}=scheduler();
        tick();tick();
        assert.deepEqual(calls,[{mode:'recognize',keys:['a.pdf','b.pdf']}]);
        s.hasRunningBackgroundJobs=true;
        s.pendingFiles.push({name:'c.pdf'});s.autoProcessKeysRef.current.add('c.pdf');tick();
        assert.equal(calls.length,1);
        s.hasRunningBackgroundJobs=false;s.recognitionReview={kind:'queue-recognition'};tick();
        assert.equal(calls.length,1,'do not overlap the conversion and save transactions');
        s.recognitionReview=null;tick();
        assert.deepEqual(calls[1],{mode:'recognize',keys:['c.pdf']});
        assert.equal(s.autoProcessKeysRef.current.size,0);
        """)

    def test_restore_errors_and_local_edits_delay_without_losing_pending_work(self):
        run_node(SETUP + r"""
        const {sandbox:s,calls,tick}=scheduler();
        for(const key of ['initialSessionLoaded','reviewEditorActive','operationRecovery']) {
          s.initialSessionLoaded=true;s.reviewEditorActive=false;s.operationRecovery=null;
          s[key]=key==='initialSessionLoaded'?false:true;tick();
          assert.equal(calls.length,0);assert.equal(s.autoProcessKeysRef.current.size,2);
        }
        s.operationRecovery=null;tick();assert.equal(calls.length,1);
        """)

    def test_append_preserves_inflight_generation_but_removal_invalidates_it(self):
        run_node(NODE_SETUP + r"""
        const a={name:'a'},b={name:'b'};
        const s={useCallback:fn=>fn,fileQueueKey:file=>file.name,
          pendingFilesRef:{current:[a]},pendingFileKeysRef:{current:new Set(['a'])},
          queueGenerationRef:{current:0},setPendingFiles(){}};
        vm.createContext(s);
        vm.runInContext(section('  const setPendingFilesTracked = useCallback(', '  useEffect(() => {\n    if (!pendingFiles.length)')
          +'\nglobalThis.update=setPendingFilesTracked;globalThis.current=queueRequestIsCurrent;',s);
        s.update([a,b]);assert.equal(s.current(0,['a']),true);
        s.update([b]);assert.equal(s.current(0,['a']),false);
        assert.equal(s.queueGenerationRef.current,1);
        """)


if __name__ == '__main__':
    unittest.main()
