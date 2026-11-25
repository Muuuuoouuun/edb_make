const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const path=require('node:path'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const root=process.env.PNG_MAKER_TEST_DIR,url=process.env.PNG_MAKER_TEST_URL,filename='분수 연습.v2.png',prefix='분수 연습.v2_변환';
const pngName=i=>prefix+'_문제'+String(i).padStart(2,'0')+'.png';
async function tabTo(page,locator){for(let i=0;i<160;i++){if(await locator.evaluate(e=>e===document.activeElement))return;await page.keyboard.press('Tab');}throw Error('Tab could not reach '+locator);}
async function inspect(p){return p.locator('.region').evaluateAll(nodes=>{const c=document.querySelector('#source');return nodes.map(n=>({id:n.dataset.id,flag:n.classList.contains('margin-mark'),rect:['left','top','width','height'].map((k,i)=>Math.round(parseFloat(n.style[k])*(i%2?c.height:c.width)/100))}));});}
async function test(browser,tag,mobile){
 const folder=path.join(root,tag),out=path.join(folder,mobile?'390':'1440');await fs.mkdir(out,{recursive:true});
 const ctx=await browser.newContext({viewport:{width:mobile?390:1440,height:mobile?844:1000},acceptDownloads:true});
 const p=await ctx.newPage(),errors=[],requests=[];p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>requests.push(r));
 await p.goto(url,{waitUntil:'networkidle'});
 const act=async loc=>{if(mobile){await tabTo(p,loc);await p.keyboard.press('Enter');}else await loc.click();};
 const input=async(sel,value)=>{const loc=p.locator(sel);if(mobile){await tabTo(p,loc);await p.keyboard.press('ControlOrMeta+a');await p.keyboard.type(String(value));}else await loc.fill(String(value));};
 async function check(){const cb=p.locator('#reviewed');if(!(await cb.isChecked())){if(mobile){await tabTo(p,cb);await p.keyboard.press('Space');}else await cb.check();}}
 async function download(selector,name,subdir){
  const dpath=path.join(out,subdir);await fs.mkdir(dpath,{recursive:true});const wait=p.waitForEvent('download');await act(p.locator(selector));const d=await wait;
  assert.equal(d.suggestedFilename(),name);await d.saveAs(path.join(dpath,d.suggestedFilename()));
 }
 const choosing=p.waitForEvent('filechooser');await act(p.locator('#home .upload'));await (await choosing).setFiles(path.join(folder,filename));await p.locator('#editor').waitFor();
 const truth=JSON.parse(await fs.readFile(path.join(folder,'truth.json'),'utf8')),raw=await inspect(p);
 const match=(a,b)=>a.every((v,i)=>v===b[i]);
 const original=truth.map(rect=>{const r=raw.find(r=>match(rect,r.rect));assert(r,'missing auto rect '+rect);return r;});
 const row=id=>p.locator('.row[data-id="'+id+'"] .row-name');
 if(tag==='lined'){
  assert(original[0].flag&&original[6].flag);
  for(const q of [0,6]){await act(row(original[q].id));await act(p.locator('#keep-margin'));}
  assert.equal(await p.locator('.margin-mark').count(),2);await act(p.locator('#exclude-margins'));
 }else{assert.equal(raw.length,7);assert.equal(raw.filter(r=>r.flag).length,0);}
 const before=await inspect(p);assert.deepEqual(before.map(r=>r.id),[0,1,3,2,4,5,6].map(i=>original[i].id));
 await check();await download('#export-all',prefix+'.zip','before');
 await act(p.locator('#range-controls summary'));await input('#range-first',2);await input('#range-last',5);
 await act(p.locator('#range-sort'));const after=await inspect(p);assert.deepEqual(after.map(r=>r.id),original.map(r=>r.id));
 for(const i of [0,5,6])assert.deepEqual(after[i],before[i]);assert(!(await p.locator('#reviewed').isChecked()));
 await act(p.locator('#undo'));assert.deepEqual(await inspect(p),before);await act(p.locator('#range-sort'));assert.deepEqual(await inspect(p),after);
 await check();await download('#export-all',prefix+'.zip','final');
 for(let i=0;i<after.length;i++){await act(row(after[i].id));await download('#export-one',pngName(i+1),'single');}
 assert(!/\bR\d{3,}\b/.test(await p.locator('#editor').innerText()));assert.equal(await p.getByRole('button',{name:/R\d{3,}/}).count(),0);
 assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
 assert(requests.every(r=>r.url().startsWith(url)&&r.method()==='GET'&&!r.postData()));

 const report={tag,width:mobile?390:1440,raw,before,after,truth,sortedRange:[2,5],outsideOrdinals:[1,6,7],singleNames:after.map((_,i)=>pngName(i+1)),zip:prefix+'.zip',browser:await browser.version()};
 await fs.writeFile(path.join(out,'ui.json'),JSON.stringify(report,null,2));console.log(tag,report.width,'PASS',after.map(r=>r.id).join(','));
 await ctx.close();
}
(async()=>{const b=await chromium.launch({channel:'chrome',headless:true});try{
 for(const tag of ['lined','plain'])for(const mobile of [false,true])await test(b,tag,mobile);
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
