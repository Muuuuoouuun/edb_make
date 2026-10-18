"""Source navigation must not conflate identical names or alter queue order."""
import unittest

from test_ui_reorder_helper import run_node


SETUP = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
const sandbox = {}; vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf('function sourceReferenceParts('), source.indexOf('function SourceFilePicker('))
  + source.slice(source.indexOf('function fileQueueKey('), source.indexOf('function appendUniquePendingFiles(')), sandbox);
const entries = (session, items = [], pending = []) => JSON.parse(JSON.stringify(sandbox.sourceFileNavigationEntries(session, items, pending)));
"""


class TestUiSourceFiles(unittest.TestCase):
    def test_duplicate_basenames_require_an_unambiguous_original_path(self):
        run_node(SETUP + r"""
        const session = {input_files:['/a/시험.pdf','/b/시험.pdf'], pages:[
          {id:'p1',metadata:{original_source_path:'/a/시험.pdf'}},
          {id:'p2',source_file_name:'시험.pdf'},
          {id:'p3',metadata:{original_source_path:'/b/시험.pdf'}},
        ]};
        const result = entries(session, ['p1','p2','p3'].map((sourcePageId,i)=>({id:'q'+i,sourcePageId})));
        assert.deepEqual(result.map(e=>e.problemIds), [['q0'],['q2'],['q1']]);
        assert.equal(result[2].name,'원본 연결 미확인');
        assert.equal(result[0].name,result[1].name);
        assert.notEqual(result[0].key,result[1].key);
        """)

    def test_uploaded_digest_links_images_and_full_korean_names_without_guessing_source_index(self):
        run_node(SETUP + r"""
        const digest = 'a'.repeat(64), filename='긴_한글_원본_문제집.pdf';
        const result = entries({inputFiles:[`C:\\uploads\\${digest}_${filename}`,'/b/다른.pdf'],pages:[
          {id:'p',sourceImageUri:'http://localhost/api/file?path='+encodeURIComponent('/cache/'+digest+'_page.png'),sourceIndex:1},
        ]},[{id:'q',source:'p'}]);
        assert.equal(result[0].name,filename); assert.deepEqual(result[0].problemIds,['q']);
        assert.deepEqual(result[1].problemIds,[]);
        assert.equal(sandbox.sourceReferenceParts('/source/real_0123456789.pdf').name,'real_0123456789.pdf');
        const named = entries({input_files:[`/uploads/${digest}_${filename}`,'/uploads/다른.pdf'],
          pages:[{id:'p',source_file_name:filename}]},[{id:'q',sourcePageId:'p'}]);
        assert.deepEqual(named[0].problemIds,['q'],'legacy original filename also resolves uniquely');
        """)

    def test_legacy_pages_without_input_files_remain_separate_sources(self):
        run_node(SETUP + r"""
        const result = entries({pages:[
          {id:'p1',metadata:{source_pdf_path:'/a/첫째.pdf'}},
          {id:'p2',metadata:{source_pdf_path:'/b/둘째.pdf'}},
        ]},[{id:'q1',sourcePageId:'p1'},{id:'q2',sourcePageId:'p2'}]);
        assert.deepEqual(result.map(e=>e.problemIds),[['q1'],['q2']]);
        """)

    def test_pending_replacement_preserves_position_and_rejects_duplicate_or_missing_targets(self):
        run_node(SETUP + r"""
        const a={name:'a.pdf',size:10,lastModified:1},b={name:'b.pdf',size:20,lastModified:2},c={name:'c.pdf',size:30,lastModified:3};
        const files=[a,b],key=sandbox.fileQueueKey(a);
        const replaced=sandbox.replacePendingSourceFile(files,key,c);
        assert.equal(replaced[0],c);assert.equal(replaced[1],b);assert.equal(files[0],a);
        assert.throws(()=>sandbox.replacePendingSourceFile(files,key,b),/이미 대기열/);
        assert.throws(()=>sandbox.replacePendingSourceFile(files,'missing',c),/대기열에 없습니다/);
        const result=entries({},[],files); assert.equal(result.length,2);
        assert.ok(result.every(e=>e.pending));
        """)
