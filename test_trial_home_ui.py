"""Exercise the shared upload controller against failures without network access."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HARNESS = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function createApp({config = {}, demo = false} = {}) {
  const elements = new Map(), requests = [], replies = [], scripts = [], timers = new Map();
  let nextTimer = 0, activeDemo = demo;
  const makeElement = () => ({hidden:false, disabled:false, value:'', dataset:{}, style:{}, children:[], handlers:{},
    classList:{add(){},remove(){},toggle(){}},
    setAttribute(key,value){this[key]=value;},
    addEventListener(type, fn){this.handlers[type]=fn;},
    replaceChildren(){this.children=[];}, appendChild(child){this.children.push(child);},
    querySelectorAll(){return [];}, showModal(){this.open=true;}, close(){this.open=false;}});
  const element = id => {if(!elements.has(id)) elements.set(id, makeElement());return elements.get(id);};
  for (const id of ['view-processing','view-result','upload-retry','retry-notice']) element(id).hidden=true;
  const aborted = signal => new Promise((_, reject) => signal.addEventListener('abort', () => {
    const error = new Error('aborted');error.name='AbortError';reject(error);
  }, {once:true}));
  const sandbox = {
    window:{TRIAL_LOGIC:require('./public/trial_logic.js'), scrollTo(){}},
    document:{body:{dataset:{trialMode:demo?'demo':'public'}}, getElementById:element,
      createElement:makeElement, addEventListener(){}, head:{appendChild(script){scripts.push(script);}}},
    navigator:{sendBeacon(){return true;}}, Blob, AbortController, performance:{now:()=>0},
    setTimeout(fn, ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},
    clearTimeout(id){timers.delete(id);}, setInterval(){return 1;}, clearInterval(){},
    fetch(url, options) {
      requests.push({url, options});
      const reply = replies.shift();
      if (!reply) throw new Error('unexpected fetch '+url);
      if (reply instanceof Error) return Promise.reject(reply);
      if (reply.pending) return aborted(options.signal);
      return Promise.resolve({ok:reply.status>=200 && reply.status<300,status:reply.status,
        text:()=>reply.pendingBody?aborted(options.signal):Promise.resolve(JSON.stringify(reply.body))});
    }
  };
  if (demo) sandbox.window.TRIAL_DEMO = {
    initialize(reset){this.reset=reset;return Promise.resolve(config);},
    canParse(){return activeDemo;}, setBusy(value){element('demo-logout').disabled=value;},
    handleResponse(status){if(status!==401)return false;activeDemo=false;this.reset();return true;}
  };
  else replies.push({status:200,body:config});
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('public/app.js','utf8'), sandbox);
  const flush = async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
  return {element,requests,replies,scripts,timers,sandbox,flush,
    upload(file={name:'exam.pdf',type:'application/pdf',size:100}) {
      element('file-input').files=[file];return element('file-input').handlers.change();
    },
    expire(ms) {const entry=[...timers.entries()].find(([,timer])=>timer.ms===ms);assert.ok(entry,'timer '+ms);timers.delete(entry[0]);entry[1].fn();}
  };
}
const valid = {pages:[],problems:[],processed_page_count:1,source_page_count:1,remaining_today:2};
'''


def run_app(script: str) -> None:
    subprocess.run(
        ["node", "-e", HARNESS + "\n(async()=>{\n" + script + "\n})().catch(e=>{console.error(e);process.exitCode=1;});"],
        cwd=ROOT,
        check=True,
    )


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestTrialHomeUI(unittest.TestCase):
    def test_malformed_response_recovers_and_retries_same_file_in_both_modes(self):
        run_app(r'''
for (const demo of [false,true]) {
  const app=createApp({demo});await app.flush();
  app.replies.push({status:200,body:{pages:[]}});
  await app.upload();
  assert.equal(app.element('view-processing').hidden,true);
  assert.equal(app.element('view-upload').hidden,false);
  assert.match(app.element('upload-message').textContent,/결과를 불러오지/);
  assert.equal(app.element('upload-retry').hidden,false);
  assert.equal(app.element('retry-notice').hidden,demo);
  assert.equal(app.element('file-input').disabled,false);
  assert.equal(app.element('file-input').value,'');
  app.replies.push({status:200,body:valid});
  await app.element('upload-retry').handlers.click();
  assert.equal(app.element('view-result').hidden,false);
  assert.equal(app.element('upload-retry').hidden,true);
  const posts=app.requests.filter(r=>r.options.method==='POST');
  assert.equal(posts.length,2);
  assert.equal(posts[0].options.body,posts[1].options.body);
  assert.equal(posts[1].url,demo?'/api/demo/parse':'/api/parse');
  assert.equal(posts[1].options.headers['X-Demo-Request'],demo?'1':undefined);
  assert.equal(app.timers.size,0);
}
''')

    def test_stalled_request_or_body_times_out_without_duplicate_upload(self):
        run_app(r'''
for (const pendingBody of [false,true]) {
  const app=createApp();await app.flush();
  app.replies.push(pendingBody?{status:200,pendingBody:true}:{pending:true});
  const upload=app.upload();await app.flush();
  assert.equal(app.element('file-input').disabled,true);
  await app.upload({name:'ignored.pdf',type:'application/pdf',size:100});
  assert.equal(app.requests.filter(r=>r.options.method==='POST').length,1);
  app.expire(90000);await upload;
  assert.equal(app.element('view-upload').hidden,false);
  assert.match(app.element('upload-message').textContent,/처리 시간이 길어져/);
  assert.equal(app.element('upload-retry').hidden,false);
  assert.equal(app.element('file-input').disabled,false);
  assert.equal(app.timers.size,0);
}
''')

    def test_turnstile_load_failure_releases_waiter_and_retry_reloads_widget(self):
        run_app(r'''
const app=createApp({config:{turnstile_site_key:'test-site-key'}});await app.flush();
const first=app.upload();await app.flush();
assert.equal(app.element('file-input').disabled,true);
app.scripts[0].onerror();await first;
assert.equal(app.element('file-input').disabled,false);
assert.equal(app.element('upload-retry').hidden,false);
assert.equal(app.element('retry-notice').hidden,true);
assert.equal(app.timers.size,0);
assert.equal(app.requests.length,1,'no parse without a token');
let callbacks;
app.sandbox.window.turnstile={render(target, options){callbacks=options;return 'widget';},reset(){}};
const retry=app.element('upload-retry').handlers.click();await app.flush();
assert.equal(app.scripts.length,2);
app.scripts[1].onload();
app.replies.push({status:200,body:valid});callbacks.callback('verified-token');await retry;
assert.equal(app.requests.at(-1).options.headers['x-turnstile-token'],'verified-token');
assert.equal(app.element('view-result').hidden,false);
assert.equal(app.timers.size,0);
''')

    def test_failed_config_never_parses_with_token_free_defaults(self):
        run_app(r'''
const app=createApp({config:null});await app.flush();
app.replies.push({pending:true});
const upload=app.upload();await app.flush();app.expire(15000);await upload;
assert.equal(app.requests.filter(r=>r.options.method==='POST').length,0);
assert.equal(app.element('upload-retry').hidden,false);
assert.equal(app.element('retry-notice').hidden,true);
app.replies.push({status:200,body:{}},{status:200,body:valid});
await app.element('upload-retry').handlers.click();
assert.equal(app.element('view-result').hidden,false);
assert.equal(app.timers.size,0);
''')

    def test_demo_auth_expiry_clears_retry_file(self):
        run_app(r'''
const app=createApp({demo:true});await app.flush();
app.replies.push({status:200,body:{pages:[]}});await app.upload();
assert.equal(app.element('upload-retry').hidden,false);
app.replies.push({status:401,body:{error:{code:'demo_auth_required'}}});
await app.element('upload-retry').handlers.click();
assert.equal(app.element('upload-retry').hidden,true);
assert.equal(app.element('retry-notice').hidden,true);
assert.equal(app.element('demo-logout').disabled,false);
assert.equal(app.element('pages').children.length,0);
''')


if __name__ == "__main__":
    unittest.main()
