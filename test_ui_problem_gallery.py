from __future__ import annotations

import unittest

from test_ui_home_redesign import HOME_SETUP
from test_ui_home_flows import NODE_SETUP, run_node


GALLERY_SETUP = NODE_SETUP + r"""
const Babel = require('./scripts/vendor/babel.min.js');
let hooks = [], cursor = 0, effects = [], requests = [], downloads = [];
let serverJobs = [], stopAfterBatch = false, nextCalls = 0, noProgress = false, failExport = false, failLabels = false, unknownLabels = false;
const preferences = {};
const makeProblem = (number, jobId = 'source') => ({
  id: `${jobId}:p${number}`, jobId, title: `Question ${number}`, number,
  problemNumberSource: 'ocr_top_left',
  pageNumbers: [number], sourceName: `${jobId}.pdf`, riskFlags: number === 2 ? ['check'] : [],
  imageUrl: `/image/${number}`, previewUrl: `/preview/${number}`, width: 30, height: 40,
});
const sandbox = {
  React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
  Icon: new Proxy({}, { get: (_object, key) => key }),
  useState(initial) {
    const index = cursor++;
    if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
    return [hooks[index], update => { hooks[index] = typeof update === 'function' ? update(hooks[index]) : update; }];
  },
  useRef(initial) { const index = cursor++; return hooks[index] ||= { current: initial }; },
  useMemo: fn => fn(), useEffect: fn => effects.push(fn),
  initialTweakValues: () => ({ dark: false, accent: '#2f6fed' }),
  PRIMARY_MODIFIER_LABEL: 'Ctrl', clearTimeout, setTimeout,
  originalProblemNumberInfo: require('./ui_prototype/reorder.js').originalProblemNumberInfo,
  readStoredPreference: (key, fallback, validate) => validate(preferences[key]) ? preferences[key] : fallback,
  writeStoredPreference: (key, value) => { preferences[key] = value; },
  fileQueueKey: file => file.name,
  BrandMark() {}, App() {},
  expectOkJson: async response => response.json(),
  document: {
    body: { classList: { toggle() {} }, appendChild() {} },
    documentElement: { style: { setProperty() {} } },
    createElement: () => ({ click() { downloads.push(this.href); }, remove() {} }),
  },
  fetch: async (url, options) => {
    const payload = options?.body ? JSON.parse(options.body) : null;
    requests.push({ url, payload });
    if (url.endsWith('/jobs')) return { json: async () => ({ ok: true, jobs: serverJobs }) };
    if (url.endsWith('/next')) {
      nextCalls++;
      if (stopAfterBatch) button(render(), '현재 묶음 후 중지').props.onClick();
      const old = serverJobs.find(job => job.id === payload.jobId);
      const end = noProgress ? old.processedPages : Math.min(old.processedPages + 12, old.sourcePageCount);
      const job = { ...old, processedPages: end, status: end === old.sourcePageCount ? 'complete' : 'partial',
        problems: Array.from({ length: end }, (_, index) => makeProblem(index + 1, old.id)) };
      serverJobs = serverJobs.map(existing => existing.id === job.id ? job : existing);
      return { json: async () => ({ ok: true, job }) };
    }
    if (url.endsWith('/export-images')) {
      if (failExport) throw new Error('저장 실패');
      return { json: async () => ({ ok: true, url: '/download.zip', count: payload.problemIds.length }) };
    }
    if (url.endsWith('/recognize-labels')) {
      if (failLabels) throw new Error('OCR 엔진 확인 필요');
      if (stopAfterBatch) button(render(), '현재 묶음 후 중지').props.onClick();
      serverJobs = serverJobs.map(job => ({ ...job, problems: job.problems.map(problem =>
        payload.problemIds.includes(problem.id) ? { ...problem, labelRecognitionAttempted: true, problemNumberSource: unknownLabels ? '' : 'ocr_top_left' } : problem) }));
      return { json: async () => ({ ok: true, jobs: serverJobs, recognized: unknownLabels ? 0 : payload.problemIds.length, unconfirmed: unknownLabels ? payload.problemIds.length : 0, errors: 0 }) };
    }
    throw new Error(`Unexpected endpoint ${url}`);
  },
};
vm.createContext(sandbox);
vm.runInContext(Babel.transform(section('// ─── PROBLEM-ONLY GALLERY', 'ReactDOM.createRoot('), { presets: ['react'] }).code, sandbox);
const nodes = value => !value ? [] : Array.isArray(value) ? value.flatMap(nodes)
  : typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
const text = value => !value ? '' : Array.isArray(value) ? value.map(text).join(' ')
  : typeof value === 'object' ? text(value.props?.children) : String(value);
const render = () => { cursor = 0; effects = []; return sandbox.ProblemGalleryApp({ onBack() {} }); };
const button = (tree, label) => {
  const found = nodes(tree).find(node => node.type === 'button' && text(node).replace(/\s+/g, '').includes(label.replace(/\s+/g, '')));
  assert.ok(found, `button ${label} must exist`);
  return found;
};
const mount = async () => { render(); effects[0](); await new Promise(setImmediate); return render(); };
"""


class TestUiProblemGallery(unittest.TestCase):
    def test_home_extraction_choice_navigates_without_uploading_or_starting_edb(self):
        run_node(HOME_SETUP + r"""
        let galleryOpens = 0;
        const tree = render({ onOpenGallery: () => galleryOpens++ });
        const choices = withClass(tree, 'home-purpose-card');
        assert.equal(choices.length, 2);
        choices.find(choice => text(choice).includes('문제만 추출')).props.onClick();
        assert.equal(galleryOpens, 1);
        assert.equal(calls.upload, 0);
        assert.equal(calls.sample, 0);
        """)

    def test_filter_combines_source_search_and_review_flags(self):
        run_node(GALLERY_SETUP + r"""
        const jobs = [{ problems: [makeProblem(1), makeProblem(2), makeProblem(2, 'other')] }];
        assert.deepEqual(Array.from(sandbox.filterGalleryProblems(jobs, 'source', '2', true), p => p.id), ['source:p2']);
        assert.equal(sandbox.filterGalleryProblems(jobs, 'all', 'other.pdf', false).length, 1);
        assert.equal(sandbox.filterGalleryProblems(jobs, 'all', 'absent', false).length, 0);
        """)

    def test_pause_waits_for_batch_then_resume_finishes_without_editor_requests(self):
        run_node(GALLERY_SETUP + r"""
        (async () => {
          serverJobs = [{ id: 'source', sourceName: 'source.pdf', sourcePageCount: 25,
            processedPages: 0, status: 'pending', problems: [] }];
          await mount();
          stopAfterBatch = true;
          await button(render(), '이어서 추출').props.onClick();
          assert.equal(nextCalls, 1);
          assert.equal(serverJobs[0].processedPages, 12);
          assert.ok(text(render()).includes('중지했습니다'));
          stopAfterBatch = false;
          await button(render(), '이어서 추출').props.onClick();
          assert.equal(nextCalls, 3);
          assert.equal(serverJobs[0].problems.length, 25);
          assert.ok(text(render()).includes('추출을 마쳤습니다'));
          assert.ok(requests.every(request => request.url.startsWith('/api/parser/')),
            'extraction must never register, publish, or replace an EDB session');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_selection_survives_pagination_and_search_and_download_uses_selection(self):
        run_node(GALLERY_SETUP + r"""
        (async () => {
          serverJobs = [{ id: 'source', sourceName: 'source.pdf', sourcePageCount: 60,
            processedPages: 60, status: 'complete', problems: Array.from({ length: 60 }, (_, i) => makeProblem(i + 1)) }];
          let tree = await mount();
          assert.equal(nodes(tree).filter(node => node.type === 'article').length, 48);
          const selectAll = nodes(tree).find(node => node.type === 'input' && node.props.type === 'checkbox');
          selectAll.props.onChange();
          button(render(), '다음').props.onClick();
          tree = render();
          const cards = nodes(tree).filter(node => node.type === 'article');
          assert.equal(cards.length, 12);
          assert.ok(cards.every(card => nodes(card).find(node => node.type === 'input').props.checked));
          nodes(tree).find(node => node.type === 'input' && node.props.type === 'search').props.onChange({ target: { value: 'Question 60' } });
          await button(render(), '선택 60개 PNG 받기').props.onClick();
          assert.equal(requests.at(-1).payload.problemIds.length, 60);
          assert.deepEqual(downloads, ['/download.zip']);
          button(render(), '60개 선택 해제').props.onClick();
          await button(render(), '전체 1개 PNG 받기').props.onClick();
          assert.deepEqual(requests.at(-1).payload.problemIds, ['source:p60']);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_all_save_entry_points_share_choice_and_failed_save_can_retry(self):
        run_node(GALLERY_SETUP + r"""
        (async () => {
          serverJobs = [{ id: 'source', sourceName: 'source.pdf', sourcePageCount: 1,
            processedPages: 1, status: 'complete', problems: [makeProblem(1)] }];
          await mount();
          const chooser = () => nodes(render()).find(node => node.type === sandbox.GallerySaveOptions);
          assert.equal(chooser().props.mode, 'raw');
          const choices = nodes(sandbox.GallerySaveOptions(chooser().props)).filter(node => node.type === 'input');
          assert.deepEqual(choices.map(node => node.props.value), ['raw', 'transparent', 'both']);
          choices[1].props.onChange();
          assert.equal(preferences['edb.gallery.saveMode'], 'transparent');
          await button(render(), '전체 1개 PNG 받기').props.onClick();
          assert.equal(requests.at(-1).payload.mode, 'transparent');
          assert.equal(requests.at(-1).payload.single, false);
          const individual = () => nodes(render()).find(node => node.props?.['aria-label'] === '1번 PNG 다운로드');
          await individual().props.onClick();
          assert.equal(requests.at(-1).payload.single, true);
          assert.equal(requests.at(-1).payload.mode, 'transparent');
          nodes(render()).find(node => node.props?.['aria-label'] === '1번 크게 보기').props.onClick();
          const preview = () => nodes(render()).find(node => node.type === sandbox.GalleryProblemPreview);
          preview().props.onModeChange('both');
          assert.equal(chooser().props.mode, 'both');
          failExport = true;
          await preview().props.onDownload();
          assert.ok(text(render()).includes('저장 실패'));
          assert.equal(button(render(), '전체 1개 PNG 받기').props.disabled, false);
          failExport = false;
          const before = requests.length;
          await Promise.all([preview().props.onDownload(), preview().props.onDownload()]);
          assert.equal(requests.length, before + 1, 'rapid repeated clicks must create one export');
          assert.equal(requests.at(-1).payload.mode, 'both');
          assert.equal(requests.at(-1).payload.single, true);
          assert.equal(downloads.length, 3);
          hooks = [];
          await mount();
          assert.equal(chooser().props.mode, 'both', 'save choice survives remount');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_stalled_response_stops_loop_and_preserves_retry(self):
        run_node(GALLERY_SETUP + r"""
        (async () => {
          serverJobs = [{ id: 'source', sourceName: 'source.pdf', sourcePageCount: 25,
            processedPages: 0, status: 'pending', problems: [] }];
          await mount();
          noProgress = true;
          await button(render(), '이어서 추출').props.onClick();
          assert.equal(nextCalls, 1);
          assert.ok(text(render()).includes('추출이 진행되지 않았습니다'));
          assert.equal(button(render(), '이어서 추출').props.disabled, false);
          noProgress = false;
          await button(render(), '이어서 추출').props.onClick();
          assert.equal(serverJobs[0].processedPages, 25);
          assert.deepEqual(requests.filter(r => r.url.endsWith('/next')).map(r => r.payload.expectedProcessedPages), [0, 0, 12, 24]);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_range_selection_crosses_pages_and_preserves_hidden_sources(self):
        run_node(GALLERY_SETUP + r"""
        const ids = Array.from({length:60}, (_,i)=>`p${i+1}`);
        let result = sandbox.gallerySelectionClick(ids, new Set(['hidden']), null, 'p46');
        result = sandbox.gallerySelectionClick(ids, result.selected, result.anchor, 'p51', {shiftKey:true});
        assert.deepEqual(Array.from(result.selected), ['hidden','p46','p47','p48','p49','p50','p51']);
        result = sandbox.gallerySelectionClick(ids, result.selected, result.anchor, 'p48', {shiftKey:true});
        assert.deepEqual(Array.from(result.selected), ['hidden','p46','p47','p48']);
        result = sandbox.gallerySelectionClick(ids, result.selected, result.anchor, 'p55', {metaKey:true});
        assert(result.selected.has('p55'));
        result = sandbox.gallerySelectionClick(['p1','p2'], result.selected, result.anchor, 'p2', {shiftKey:true});
        assert(result.selected.has('p2') && result.selected.has('hidden'));
        assert.equal(result.anchor,'p2');
        assert.deepEqual(Array.from(sandbox.galleryMarqueeHits([
          {id:'a',left:0,right:100,top:0,bottom:100},
          {id:'b',left:110,right:210,top:0,bottom:100},
          {id:'c',left:0,right:100,top:110,bottom:210},
        ],{left:50,top:40,width:80,height:60})),['a','b']);
        """)

    def test_modifier_clicks_bulk_actions_and_density_preference(self):
        run_node(GALLERY_SETUP + r"""
        (async()=>{
          serverJobs=[{id:'source',sourceName:'source.pdf',sourcePageCount:60,processedPages:60,status:'complete',
            problems:Array.from({length:60},(_,i)=>makeProblem(i+1))}];
          await mount();
          const card = n => nodes(render()).find(node=>node.props?.['aria-label']===`${n}번 크게 보기`);
          card(2).props.onClick({ctrlKey:true});
          card(5).props.onClick({shiftKey:true});
          assert.equal(nodes(render()).filter(node=>node.type==='article' && node.props.className.includes('is-selected')).length,4);
          assert(!nodes(render()).some(node=>node.type===sandbox.GalleryProblemPreview));
          button(render(),'선택 반전').props.onClick();
          await button(render(),'선택 56개 PNG 받기').props.onClick();
          assert.equal(requests.at(-1).payload.problemIds.length,56);
          button(render(),'56개 선택 해제').props.onClick();
          button(render(),'이 페이지 선택').props.onClick();
          assert.ok(text(render()).replace(/\s+/g,'').includes('48개선택해제'));
          const section = () => nodes(render()).find(node=>node.props?.className==='gallery-results');
          let prevented=0;
          const key = (key,ctrlKey,target)=>({key,ctrlKey,target:target||{closest:()=>null},preventDefault(){prevented++},stopPropagation(){}});
          section().props.onKeyDown(key('a',true));
          assert.ok(text(render()).replace(/\s+/g,'').includes('60개선택해제'));
          section().props.onKeyDown(key('Escape'));
          assert(!text(render()).includes('개 선택 해제'));
          section().props.onKeyDown(key('a',true,{closest:()=>({})}));
          assert(!text(render()).includes('개 선택 해제'),'typing shortcuts must stay in the search field');
          assert.equal(prevented,2);
          button(render(),'보통').props.onClick();
          assert.equal(preferences['edb.gallery.density'],'comfortable');
          hooks=[];await mount();
          assert.equal(render().props['data-density'],'comfortable');
        })().catch(error=>{console.error(error);process.exitCode=1;});
        """)


    def test_labels_require_evidence_and_passages_are_searchable(self):
        run_node(GALLERY_SETUP + r"""
        const label = sandbox.galleryProblemLabel;
        assert.equal(label(makeProblem(123)).label, '123번');
        assert.equal(label({...makeProblem(1),problemNumberSource:'inferred_sequence'}).label,'번호 확인 필요');
        assert.equal(label({...makeProblem(1),problemNumberSource:''}).confirmed,false);
        const passage={...makeProblem(12),passageRole:'passage_fragment',passageRange:{start:12,end:15}};
        assert.equal(label(passage).label,'지문 12~15번');
        assert.equal(label({...passage,passageRole:'child_question'}).label,'12번');
        assert.deepEqual(Array.from(sandbox.filterGalleryProblems([{problems:[passage,makeProblem(1)]}],'all','지문',false),p=>p.id),[passage.id]);
        """)

    def test_recognition_is_selected_batched_resumable_and_preserves_selection(self):
        run_node(GALLERY_SETUP + r"""
        (async()=>{
          serverJobs=[{id:'source',sourceName:'source.pdf',sourcePageCount:11,processedPages:11,status:'complete',
            problems:Array.from({length:11},(_,i)=>({...makeProblem(i+1),problemNumberSource:i===10?'pdf_text_marker':''}))}];
          await mount();
          nodes(render()).find(n=>n.type==='input'&&n.props.type==='checkbox').props.onChange();
          const run=()=>button(render(),'번호·지문 다시 인식');
          failLabels=true;await run().props.onClick();
          assert.ok(text(render()).includes('OCR 엔진 확인 필요'));
          assert.equal(run().props.disabled,false);
          failLabels=false;stopAfterBatch=true;
          await Promise.all([run().props.onClick(),run().props.onClick()]);
          assert.equal(requests.filter(r=>r.url.endsWith('/recognize-labels')).length,2);
          assert.equal(serverJobs[0].problems.filter(p=>p.problemNumberSource).length,9);
          stopAfterBatch=false;await run().props.onClick();
          assert.equal(requests.at(-1).payload.problemIds.length,2);
          assert.equal(run().props.disabled,true);
          assert.ok(text(render()).replace(/\s/g,'').includes('11개선택해제'));
          assert.equal(requests.filter(r=>r.url.endsWith('/next')).length,0);
        })().catch(error=>{console.error(error);process.exitCode=1;});
        """)

    def test_default_auto_recognition_ignores_selection_and_does_not_repeat_unreadable_crops(self):
        run_node(GALLERY_SETUP + r"""
        (async()=>{
          serverJobs=[{id:'source',sourceName:'source.pdf',sourcePageCount:11,processedPages:11,status:'complete',
            problems:Array.from({length:11},(_,i)=>({...makeProblem(i+1),problemNumberSource:i===0?'pdf_text_marker':'',labelRecognitionAttempted:i===1}))}];
          await mount();
          const auto=async()=>{render();effects.at(-1)();await new Promise(setImmediate);return render();};
          // Selecting and filtering to a known question must not limit automatic work.
          nodes(render()).find(n=>n.props?.['aria-label']==='1번 선택').props.onChange();
          nodes(render()).find(n=>n.type==='input'&&n.props.type==='search').props.onChange({target:{value:'Question 1'}});
          unknownLabels=true;
          await auto();
          let calls=requests.filter(r=>r.url.endsWith('/recognize-labels'));
          assert.deepEqual(calls.map(r=>r.payload.problemIds.length),[8,1]);
          assert(calls.every(r=>r.payload.automatic===true));
          assert.equal(calls[0].payload.problemIds[0],'source:p3');
          await auto();assert.equal(requests.filter(r=>r.url.endsWith('/recognize-labels')).length,2);
          hooks=[];await mount();await auto();
          assert.equal(requests.filter(r=>r.url.endsWith('/recognize-labels')).length,2,'refresh must reuse completed unknown attempts');
          // A new extracted batch is picked up automatically after jobs change.
          serverJobs[0].problems.push({...makeProblem(12),problemNumberSource:''});
          hooks=[];await mount();await auto();
          calls=requests.filter(r=>r.url.endsWith('/recognize-labels'));
          assert.deepEqual(calls.at(-1).payload.problemIds,['source:p12']);
          unknownLabels=false;
          await button(render(),'번호·지문 다시 인식').props.onClick();
          assert.equal(requests.at(-1).payload.automatic,false,'explicit retry remains available');
        })().catch(error=>{console.error(error);process.exitCode=1;});
        """)

    def test_automatic_failure_and_stop_do_not_restart_the_queue(self):
        run_node(GALLERY_SETUP + r"""
        (async()=>{
          serverJobs=[{id:'source',sourceName:'source.pdf',sourcePageCount:10,processedPages:10,status:'complete',
            problems:Array.from({length:10},(_,i)=>({...makeProblem(i+1),problemNumberSource:''}))}];
          const auto=async()=>{render();effects.at(-1)();await new Promise(setImmediate);render();};
          await mount();failLabels=true;await auto();await auto();
          assert.equal(requests.filter(r=>r.url.endsWith('/recognize-labels')).length,1,'network/engine errors must not loop');
          assert.ok(text(render()).includes('OCR 엔진 확인 필요'));
          hooks=[];failLabels=false;stopAfterBatch=true;await mount();await auto();await auto();
          assert.equal(requests.filter(r=>r.url.endsWith('/recognize-labels')).length,2,'stopping after one batch must stay stopped');
          assert.equal(serverJobs[0].problems.filter(p=>p.labelRecognitionAttempted).length,8);
          assert.ok(text(render()).includes('인식을 중지했습니다'));
        })().catch(error=>{console.error(error);process.exitCode=1;});
        """)


if __name__ == '__main__':
    unittest.main()
