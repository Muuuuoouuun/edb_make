const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{const b=await chromium.launch({channel:'chrome',headless:true});try{
 const ctx=await b.newContext({acceptDownloads:true}),url=process.env.PNG_MAKER_TEST_URL;
 const sample=await fs.readFile(path.join(process.env.PNG_MAKER_TEST_DIR,'four.png'));
 const results=[];
 for(const [source,stem] of [['단원  5.나.PNG','단원  5.나'],['CON.v2?.png','_CON.v2_'],['a<b>:c|d*.png','a_b__c_d_'],['.png','학습지'],['하'.repeat(160)+'.png',null]]){
  const p=await ctx.newPage();await p.goto(url);await p.locator('#upload').setInputFiles({name:source,mimeType:'image/png',buffer:sample});
  await p.locator('#editor').waitFor();await p.locator('#reviewed').check();
  const get=async id=>{const w=p.waitForEvent('download');await p.locator(id).click();return(await w).suggestedFilename();};
  const zipped=await get('#export-all'),single=await get('#export-one');
  if(stem!==null){assert.equal(zipped,stem+'_변환.zip');assert.equal(single,stem+'_변환_문제01.png');}
  else{assert(Buffer.byteLength(single)<=255);assert(single.endsWith('_변환_문제01.png'));assert.equal(zipped,single.replace('_문제01.png','.zip'));assert(!single.includes('\uFFFD'));}
  results.push({case:source.length>100?'long Korean':source,zip:zipped,png:single,pngUtf8Bytes:Buffer.byteLength(single)});await p.close();
 }
 const p=await ctx.newPage();await p.goto(url);
 const bounds=await p.evaluate(async()=>{const {outputNames}=await import('./names.js');return [2,99,100,256].map(n=>[n,outputNames('제1.장.png',n).png(1),outputNames('제1.장.png',n).png(n)]);});
 assert.deepEqual(bounds.at(-1),[256,'제1.장_변환_문제001.png','제1.장_변환_문제256.png']);
 const base=process.env.PNG_MAKER_TEST_DIR;await p.locator('#upload').setInputFiles(path.join(base,'four.png'));await p.locator('#editor').waitFor();
 const order=()=>p.locator('.row').evaluateAll(ns=>ns.map(n=>n.dataset.id));const before=await order();
 await p.locator('#range-controls summary').click();await p.locator('#range-first').fill('3');await p.locator('#range-last').fill('2');await p.locator('#range-sort').click();assert.deepEqual(await order(),before);
 await p.locator('#reviewed').check();await p.locator('#upload').setInputFiles(path.join(base,'oversize.png'));await p.getByRole('status').filter({hasText:'1,600만'}).waitFor();assert.deepEqual(await order(),before);assert(await p.locator('#reviewed').isChecked());
 await p.locator('#upload').setInputFiles(path.join(base,'alpha.png'));await p.locator('#replace-dialog').waitFor();await p.keyboard.press('Escape');assert(await p.locator('#reviewed').isChecked());assert.deepEqual(await order(),before);
 await fs.writeFile(path.join(process.env.PNG_MAKER_TEST_DIR,'edges.json'),JSON.stringify({results,bounds,invalidRangePreserved:true,cancelAndOversizePreserved:true},null,2));
 console.log('Browser names, numerical width, invalid-range and file rejection/cancellation passed.');
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
