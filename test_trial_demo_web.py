"""Behavior checks for the password gate without a browser dependency."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestDemoWeb(unittest.TestCase):
    def test_authentication_expiry_errors_and_logout(self):
        script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {hidden:false, value:'', disabled:false, handlers:{}, attributes:{},
    addEventListener(event, fn) {this.handlers[event]=fn;},
    setAttribute(name, value) {this.attributes[name]=value;}});
  return elements.get(id);
};
const assertMessage = (pattern, error = false) => {
  const message = element('demo-message');
  assert.match(message.textContent, pattern);
  assert.equal(message.hidden, false);
  assert.equal(message.className, error ? 'notice' : 'hint');
  assert.equal(message.attributes.role, error ? 'alert' : 'status');
};
const requests = [];
const replies = [];
const windowHandlers = {};
let timer;
const sandbox = {
  window:{addEventListener(event, fn) {windowHandlers[event]=fn;}},
  document:{getElementById:element,addEventListener(){}}, Date, Number, Math, JSON,
  setTimeout(fn) { timer=fn; return 1; }, clearTimeout() {},
  fetch(url, options) {
    requests.push({url, options});
    const reply = replies.shift();
    if (reply instanceof Error) return Promise.reject(reply);
    return Promise.resolve({ok:reply.status>=200 && reply.status<300,status:reply.status,
      json:async()=>reply.body,text:async()=>JSON.stringify(reply.body)});
  }
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('public/demo.js','utf8'), sandbox);
const demo=sandbox.window.TRIAL_DEMO;
const end=new Date(Date.now()+3600000).toISOString();
(async()=>{
  replies.push({status:200,body:{active:true,authenticated:false,ends_at:end}});
  let resets=0;
  await demo.initialize(()=>resets++);
  assert.equal(demo.canParse(),false);
  assert.equal(element('demo-app').hidden,true);
  assert.equal(element('demo-login').hidden,false);
  assertMessage(/안내받은 시연 비밀번호/);
  element('demo-password').value='demo-test-secret';
  replies.push({status:401,body:{error:{code:'demo_auth_required'}}});
  const badLogin=element('demo-login').handlers.submit({preventDefault(){}});
  assert.equal(element('demo-password').value,'','password cleared before response');
  assertMessage(/비밀번호를 확인하고 있어요/);
  await badLogin;
  assert.equal(demo.canParse(),false);
  assertMessage(/비밀번호를 확인해 주세요/,true);
  element('demo-password').value='demo-test-secret';
  replies.push({status:429,body:{error:{code:'throttled'}}});
  await element('demo-login').handlers.submit({preventDefault(){}});
  assertMessage(/요청이 많아요/,true);
  replies.push(new Error('offline'));
  const retryLogin=element('demo-login').handlers.submit({preventDefault(){}});
  assertMessage(/비밀번호를 확인하고 있어요/);
  await retryLogin;
  assertMessage(/서버에 연결하지 못했어요/,true);
  replies.push({status:200,body:{authenticated:false}});
  await element('demo-login').handlers.submit({preventDefault(){}});
  assert.equal(demo.canParse(),false);
  assertMessage(/인증을 확인하지 못했어요/,true);
  replies.push({status:200,body:{authenticated:true,ends_at:end}});
  await element('demo-login').handlers.submit({preventDefault(){}});
  assert.equal(demo.canParse(),true);
  assert.equal(element('demo-app').hidden,false);
  assert.match(element('demo-expiration').textContent,/한국시간/);
  const loginRequest=requests.find(r=>r.url==='/api/demo/login');
  assert.equal(loginRequest.options.credentials,'same-origin');
  assert.equal(loginRequest.options.headers['X-Demo-Request'],'1');
  assert.deepEqual(JSON.parse(loginRequest.options.body),{password:'demo-test-secret'});
  assert.equal(demo.handleResponse(401,'{"error":{"code":"demo_auth_required"}}'),true);
  assert.equal(demo.canParse(),false);
  assert.equal(element('demo-app').hidden,true);
  assertMessage(/인증이 만료/);
  replies.push({status:200,body:{authenticated:true,ends_at:end}});
  await element('demo-login').handlers.submit({preventDefault(){}});
  demo.setBusy(true);
  assert.equal(element('demo-logout').disabled,true);
  demo.setBusy(false);
  replies.push({status:204});
  await element('demo-logout').handlers.click();
  assert.equal(demo.canParse(),false);
  assertMessage(/로그아웃했어요/);
  assert.equal(requests.at(-1).options.headers['X-Demo-Request'],'1');
  for (const reply of [{status:503}, new Error('offline')]) {
    replies.push({status:200,body:{authenticated:true,ends_at:end}});
    await element('demo-login').handlers.submit({preventDefault(){}});
    replies.push(reply);
    await element('demo-logout').handlers.click();
    assert.equal(demo.canParse(),false);
    assertMessage(/로그아웃을 완료하지 못했어요/,true);
  }
  replies.push({status:200,body:{authenticated:true,ends_at:new Date(Date.now()-1000).toISOString()}});
  await element('demo-login').handlers.submit({preventDefault(){}});
  assert.equal(demo.canParse(),false);
  assert.equal(element('demo-login').hidden,true);
  assertMessage(/기간이 끝났어요/);
  replies.push(new Error('config offline'));
  windowHandlers.pageshow({persisted:true});
  assertMessage(/시연 인증을 다시 확인/);
  await new Promise(resolve=>setImmediate(resolve));
  assertMessage(/시연 정보를 불러오지 못했어요/,true);
  assert.equal(element('demo-config-retry').hidden,false);
  replies.push({status:200,body:{active:true,authenticated:false,ends_at:end}});
  windowHandlers.pageshow({persisted:true});
  assertMessage(/시연 인증을 다시 확인/);
  await new Promise(resolve=>setImmediate(resolve));
  assertMessage(/안내받은 시연 비밀번호/);
  assert.equal(element('demo-config-retry').hidden,true);
  assert.equal(element('demo-login').hidden,false);
  assert.equal(demo.handleResponse(404,'{"error":{"code":"demo_unavailable"}}'),true);
  assertMessage(/지금은 시연 기간이 아니에요/);
  assert.equal(element('demo-login').hidden,true);
  assert.ok(resets>=4,'locking clears rendered results');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        subprocess.run(["node", "-e", script], cwd=ROOT, check=True)

    def test_demo_page_uses_shared_renderer_and_keeps_premium_gates(self):
        html = (ROOT / "public/demo/index.html").read_text(encoding="utf-8")
        self.assertIn('data-trial-mode="demo"', html)
        self.assertIn('id="demo-app" hidden', html)
        self.assertLess(html.index('src="/demo.js"'), html.index('src="/app.js"'))
        for feature in ("edb", "image", "edit"):
            self.assertIn(f'data-premium="{feature}"', html)
        self.assertNotIn("하루 3회", html)
        self.assertNotIn('data-premium="limit_daily"', html)
        code = (ROOT / "public/demo.js").read_text(encoding="utf-8")
        self.assertNotIn("localStorage", code)
        self.assertNotIn("sessionStorage", code)


if __name__ == "__main__":
    unittest.main()
