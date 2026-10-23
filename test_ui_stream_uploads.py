from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent


def test_large_files_upload_sequentially_with_bounds_and_cancel():
    subprocess.run(["node", "-e", r"""
const assert = require('node:assert/strict');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
let active = 0;
let calls = [];
let failName = '';
const sandbox = {
  formatBytes: bytes => `${bytes / 1048576}MB`,
  readJsonResponse: async response => response.payload,
  operationErrorFromResponse: payload => Object.assign(new Error(payload.error), payload),
  fetch: async (url, options) => {
    assert.equal(url, '/api/uploads');
    assert.equal(active++, 0, 'uploads must not overlap');
    assert.equal(options.headers['Content-Type'], 'application/octet-stream');
    assert.equal(options.headers['X-File-Name'], encodeURIComponent(options.body.name));
    calls.push(options.body);
    await Promise.resolve();
    active--;
    return { ok: options.body.name !== failName, payload: options.body.name === failName
      ? {ok:false,code:'runtime_storage_full',error:'디스크 공간 부족'}
      : {ok:true,sourcePath:`/uploads/${options.body.name}`} };
  },
};
vm.runInNewContext(source.slice(source.indexOf('const MAX_EXPORT_FILE_BYTES'), source.indexOf('function fileQueueKey')), sandbox);
(async () => {
  const files = [{name:'한글 1.pdf',size:200*1048576},{name:'2.pdf',size:512*1048576}];
  const progress = [];
  const result = await sandbox.uploadSourceFiles(files, {onUploadProgress:value=>progress.push(value.completed)});
  assert.deepEqual(Array.from(result), ['/uploads/한글 1.pdf','/uploads/2.pdf']);
  assert.equal(calls[0], files[0], 'send the File itself, without base64/string allocation');
  assert.deepEqual(progress, [0,1,1,2]);
  calls = [];
  await assert.rejects(sandbox.uploadSourceFiles([{name:'huge.pdf',size:513*1048576}]), error =>
    error.code === 'upload_file_too_large' && error.sourceFile === 'huge.pdf' && error.maxBytes === 512*1048576);
  await assert.rejects(sandbox.uploadSourceFiles(Array.from({length:5},(_,i)=>({name:`${i}.pdf`,size:512*1048576}))),
    error => error.code === 'upload_batch_too_large');
  assert.equal(calls.length,0,'reject before reading/uploading any file');
  assert.equal(sandbox.assertExportPayloadFits(Array.from({length:4},()=>({size:512*1048576}))),2*1024**3);
  failName = files[0].name;
  await assert.rejects(sandbox.uploadSourceFiles(files), error=>error.code==='runtime_storage_full');
  assert.equal(calls.length,1,'stop after failed file');
  calls=[];
  const controller = new AbortController(); controller.abort();
  await assert.rejects(sandbox.uploadSourceFiles(files,{signal:controller.signal}),error=>error.name==='AbortError');
  assert.equal(calls.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""], cwd=ROOT, check=True)


def test_error_details_and_capacity_numbers_survive_display_and_clipboard():
    subprocess.run(["node", "-e", r"""
const assert = require('node:assert/strict');
const fs = require('fs'); const vm = require('vm');
const source = fs.readFileSync('ui_prototype/app.jsx','utf8');
const sandbox = {};
vm.runInNewContext(source.slice(source.indexOf('function formatApiError('), source.indexOf('function operationRecoveryStateTransition(')),sandbox);
const payload={error:'PDF 처리 중 앱 내부 오류가 발생했습니다.',code:'app_internal_error',errorKind:'program',
 detail:'renderer failure with image size 1200x1600',sourceFile:'자료.pdf',pageStart:11,pageEnd:20,retryable:false};
const error=sandbox.operationErrorFromResponse(payload,{status:500},'session_export','실패');
assert.equal(sandbox.operationRecoverySummary(error),payload.error);
const copied=sandbox.operationErrorClipboardText(error);
for (const text of ['app_internal_error','자료.pdf','11–20','renderer failure']) assert(copied.includes(text),text);
const capacity=sandbox.operationErrorFromResponse({error:'파일 600MB가 한도 512MB를 초과했습니다.',code:'upload_file_too_large',
 errorKind:'capacity',receivedBytes:600*1048576,maxBytes:512*1048576},{status:413},'upload','실패');
assert(sandbox.operationRecoverySummary(capacity).includes('600MB'));
assert(sandbox.operationErrorClipboardText(capacity).includes(`${600*1048576} bytes`));
"""], cwd=ROOT, check=True)
