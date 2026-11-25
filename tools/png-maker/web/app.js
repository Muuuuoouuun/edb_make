import {propose,zip} from './core.js';
import {outputNames} from './names.js';
import {worksheet} from './demo.js';

const $=s=>document.querySelector(s);
let canvas=$('#source');
const overlay=$('#overlays'), picker=$('#upload');
const sample=worksheet(), hero=$('#hero-sheet');
hero.append(sample.cloneNode());hero.firstChild.getContext('2d').drawImage(sample,0,0);
for(const [i,[x,y]] of [[40,38],[500,38],[40,354],[500,354]].entries()){
  const outline=document.createElement('div');outline.className='sample-outline';
  Object.assign(outline.style,{left:x/960*100+'%',top:y/680*100+'%',width:420/960*100+'%',height:286/680*100+'%'});
  const badge=document.createElement('b');badge.textContent='0'+(i+1);outline.append(badge);hero.append(outline);
}
let regions=[],active=null,serial=1,history=[],drawing=false,drag=null,ready=false,loadToken=0,exporting=false;
const originalMargins=new Map();
const matchesMargin=r=>{const box=originalMargins.get(r.id);return box && keys.every(k=>box[k]===r[k]);};
const isMargin=r=>matchesMargin(r)&&!r.keepMargin;
let documentRevision=0,proposalCount=0,reviewed=false;
const keys=['x','y','w','h'],fields={};
for(const [i,k] of keys.entries()){
  const label=document.createElement('label');label.textContent=['X','Y','너비','높이'][i];
  const f=document.createElement('input');f.type='number';f.required=true;f.step='1';f.min=i<2?'0':'1';f.id='coord-'+k;
  fields[k]=f;label.append(f);$('#numbers').append(label);
}
const say=t=>{$('#status').textContent=t;};
const selected=()=>regions.find(r=>r.id===active);
const questionLabel=id=>'문제 '+String(regions.findIndex(r=>r.id===id)+1).padStart(2,'0');
const uid=()=>'R'+String(serial++).padStart(3,'0');
function capture(){return {regions:regions.map(r=>({...r})),active};}
function remember(state=capture()){reviewed=false;history.push(state);if(history.length>60)history.shift();}
function route(){
  const edit=location.hash==='#edit' && ready;
  $('#home').hidden=edit;$('#editor').hidden=!edit;
  if(!edit && ready && !$('#resume')){
    const b=document.createElement('button');b.id='resume';b.className='secondary';b.textContent='편집 계속';b.onclick=()=>{location.hash='edit';};$('.actions').append(b);
  }
}
addEventListener('hashchange',()=>{say('');route();});
document.querySelectorAll('.upload').forEach(b=>b.onclick=()=>picker.click());
function showSource(source,name){
  const next=document.createElement('canvas');next.id='source';next.width=source.width;next.height=source.height;
  const ctx=next.getContext('2d',{willReadFrequently:true});ctx.drawImage(source,0,0);
  const result=propose(ctx.getImageData(0,0,next.width,next.height));
  documentRevision++;proposalCount=result.rects.length;reviewed=false;
  canvas.replaceWith(next);canvas.width=canvas.height=1;canvas=next;
  $('#sheet').style.width='min(100%, '+(64*source.width/source.height)+'vh)';
  say('');
  serial=1;originalMargins.clear();
  const margins=new Set(result.margins);
  regions=result.rects.map(r=>{
    const region={...r,id:uid()};
    if(margins.has(r))originalMargins.set(region.id,{...r});
    return region;
  });active=regions[0]?.id??null;
  drawing=false;drag=null;history=[];ready=true;
  $('#range-first').value='';$('#range-last').value='';$('#range-controls').open=false;
  $('#file-title').textContent=name;$('#image-size').textContent=canvas.width+' × '+canvas.height+' px';
  location.hash='edit';route();render();$('#file-title').focus();
  $('#tip').textContent=result.truncated?'제안 한도 256개에 도달했습니다. 필요한 영역을 직접 확인하세요.':regions.length?'영역을 끌어 이동하고 선택 테두리의 모서리로 크기를 조절하세요.':'제안된 영역이 없습니다. ‘영역 그리기’로 문항을 추가하세요.';
}
async function allowReplacement(name){
  if(!ready)return true;
  const dialog=$('#replace-dialog');
  $('#replace-description').textContent='현재 파일 ‘'+$('#file-title').textContent+'’의 편집과 순서가 지워지고 ‘'+name+'’을 엽니다. 현재 결과가 필요하면 먼저 다운로드하세요.';
  dialog.returnValue='cancel';
  return new Promise(resolve=>{
    dialog.addEventListener('close',()=>resolve(dialog.returnValue==='replace'),{once:true});
    dialog.showModal();
  });
}
$('.demo').onclick=async()=>{
  const token=++loadToken;
  if(await allowReplacement('기하와 그래프.png') && token===loadToken)showSource(sample,'기하와 그래프.png');
};
picker.onchange=async()=>{
  const file=picker.files[0];picker.value='';if(!file)return;
  const token=++loadToken;
  try{
    if(file.size>64*1024*1024)throw Error('64 MiB 이하의 PNG를 선택하세요.');
    const a=new Uint8Array(await file.slice(0,24).arrayBuffer());
    if(a.length<24 || ![137,80,78,71,13,10,26,10].every((v,i)=>a[i]===v) || String.fromCharCode(...a.slice(12,16))!=='IHDR')throw Error('PNG 형식의 이미지를 선택하세요.');
    const view=new DataView(a.buffer),w=view.getUint32(16),h=view.getUint32(20);
    if(!w||!h||w*h>16000000 || Math.max(w,h)>16384)throw Error('1,600만 픽셀 이하, 한 변 16,384px 이하로 해상도를 낮춘 PNG 사본을 선택하세요.');
    const bitmap=await createImageBitmap(file);
    try{
      if(token===loadToken && await allowReplacement(file.name) && token===loadToken)showSource(bitmap,file.name);
    }finally{bitmap.close();}
  }catch(e){if(token===loadToken)say(e.message);}
};
const pct=(n,base)=>100*n/base+'%';
function position(el,r){
  Object.assign(el.style,{left:pct(r.x,canvas.width),top:pct(r.y,canvas.height),width:pct(r.w,canvas.width),height:pct(r.h,canvas.height)});
}
function paintBoxes(){
  overlay.replaceChildren();
  regions.forEach((r,i)=>{
    const el=document.createElement('div');el.className='region'+(r.id===active?' active':'');el.dataset.id=r.id;el.classList.toggle('margin-mark',Boolean(isMargin(r)));
    position(el,r); const label=document.createElement('b');label.textContent=String(i+1).padStart(2,'0');el.append(label);
    if(r.id===active)for(const side of ['nw','ne','sw','se']){
      const handle=document.createElement('i');handle.className='handle '+side;handle.dataset.side=side;el.append(handle);
    }overlay.append(el);
  });overlay.classList.toggle('drawing',drawing);
}
function button(text,label,handler){
  const b=document.createElement('button');b.textContent=text;b.setAttribute('aria-label',label);b.title=label;b.onclick=handler;return b;
}
function render(){
  const focused=document.activeElement,oldRow=focused.closest('#list .row');
  const rowAction=oldRow && focused.dataset.action;
  paintBoxes();const list=$('#list');list.replaceChildren();
  $('#count').textContent=regions.length+'개';const r=selected();$('#details').hidden=!r;
  $('#proposal-note').textContent='처음 제안 '+proposalCount+'개 · 현재 영역 '+regions.length+'개. 문항 수와 경계를 원본과 대조하세요.';
  $('#reviewed').checked=reviewed;$('#reviewed').disabled=!regions.length;
  $('#review-state').textContent=reviewed?'검수 완료 · 다운로드할 수 있습니다.':'검수 후 다운로드할 수 있습니다.';
  const nMargins=regions.filter(isMargin).length;
  $('#margin-tools').hidden=!nMargins;
  $('#margin-help').textContent=nMargins?'주황 점선 '+nMargins+'개는 위·아래 구분선 바깥에 있습니다. 실제 문항은 선택한 뒤 ‘여백 제외에서 보호’를 누르세요.':'';
  $('#exclude-margins').textContent='여백 후보 '+nMargins+'개 함께 제외';
  $('#keep-margin').hidden=!r||!matchesMargin(r);
  $('#keep-margin').setAttribute('aria-pressed',String(Boolean(r?.keepMargin)));
  $('#keep-margin').textContent=r?.keepMargin?'여백 제외 보호 해제':'여백 제외에서 보호';
  $('#column-sort').disabled=regions.length<2;
  for(const f of [$('#range-first'),$('#range-last')]){f.max=regions.length;f.disabled=regions.length<2;}
  $('#range-sort').disabled=regions.length<2;
  $('#preview-export').disabled=!regions.length||exporting||!reviewed;
  $('#export-one').disabled=!r||exporting||!reviewed;$('#export-all').disabled=!regions.length||exporting||!reviewed;
  $('#undo').disabled=!history.length;$('#draw').setAttribute('aria-pressed',String(drawing));
  for(const [i,region] of regions.entries()){
    const row=document.createElement('div');row.className='row'+(region.id===active?' chosen':'');row.dataset.id=region.id;
    const label=questionLabel(region.id);
    const b=button('',label+' 선택',()=>{active=region.id;drawing=false;render();});
    b.dataset.action='select';b.className='row-name';b.setAttribute('aria-pressed',String(region.id===active));
    const ordinal=document.createElement('strong');ordinal.textContent=String(i+1).padStart(2,'0');
    const info=document.createElement('span');info.textContent='문제';
    const dims=document.createElement('small');dims.textContent=(isMargin(region)?'여백 후보 · ':region.keepMargin&&matchesMargin(region)?'보호됨 · ':'')+region.w+' × '+region.h+' px';info.append(dims);b.append(ordinal,info);row.append(b);
    for(const [step,label] of [[-1,'위로'],[1,'아래로']]){
      const arrow=button(step<0?'↑':'↓',questionLabel(region.id)+' '+label,()=>{
        remember();[regions[i],regions[i+step]]=[regions[i+step],regions[i]];active=region.id;render();
      });arrow.dataset.action=step<0?'up':'down';arrow.disabled=!regions[i+step];row.append(arrow);
    }
    list.append(row);
  }
  if(oldRow){
    const row=list.querySelector('[data-id="'+oldRow.dataset.id+'"]') || list.querySelector('.chosen');
    const buttons=row && [...row.querySelectorAll('button')];
    (buttons?.find(b=>!b.disabled && b.dataset.action===rowAction) || buttons?.[0] || $('#add')).focus({preventScroll:true});
  }
  if(!regions.length)list.textContent='아직 영역이 없습니다.';
  if(r){for(const k of keys)fields[k].value=r[k];$('#split-h').disabled=r.h<2;$('#split-v').disabled=r.w<2;}
}
$('#reviewed').onchange=e=>{reviewed=e.target.checked;render();};
$('#keep-margin').onclick=()=>{
  const r=selected();if(!r||!matchesMargin(r))return;
  remember();r.keepMargin=!r.keepMargin;render();
  say(questionLabel(r.id)+(r.keepMargin?'를 여백 제외에서 보호했습니다.':'의 보호를 해제했습니다.'));
};
$('#exclude-margins').onclick=()=>{
  const remaining=regions.filter(r=>!isMargin(r)),count=regions.length-remaining.length;
  if(!count)return;
  remember();regions=remaining;if(!selected())active=regions[0]?.id??null;render();
  ($('#list .chosen .row-name')||$('#add')).focus({preventScroll:true});
  say('여백 후보 '+count+'개를 제외했습니다. 실행 취소로 복원할 수 있습니다.');
};
function sortColumns(start,endExclusive){
  const group=regions.slice(start,endExclusive);
  if(group.length<2)return;
  const byX=[...group].sort((a,b)=>a.x-b.x);let end=byX[0].x+byX[0].w,best=0,split=0;
  for(let i=1;i<byX.length;i++){
    const gap=byX[i].x-end;if(gap>best){best=gap;split=i;}
    end=Math.max(end,byX[i].x+byX[i].w);
  }
  if(!split){say('두 열 사이에 빈 간격이 없습니다. 각 영역의 화살표로 순서를 조정하세요.');return;}
  const topFirst=(a,b)=>a.y-b.y||a.x-b.x;
  const ordered=[...byX.slice(0,split).sort(topFirst),...byX.slice(split).sort(topFirst)];
  if(ordered.every((r,i)=>r===group[i])){say('이미 왼쪽 열부터 정렬되어 있습니다.');return;}
  remember();regions.splice(start,group.length,...ordered);render();say((start+1)+'~'+endExclusive+'번을 왼쪽 열부터 정렬했습니다.');
}
$('#column-sort').onclick=()=>sortColumns(0,regions.length);
$('#range-form').onsubmit=e=>{
  e.preventDefault();
  const first=$('#range-first').valueAsNumber,last=$('#range-last').valueAsNumber;
  if(!Number.isInteger(first)||!Number.isInteger(last)||first<1||last>regions.length||last<=first){
    say('시작과 끝 번호를 현재 목록 안에서 두 문항 이상 선택하세요.');$('#range-last').focus();return;
  }
  sortColumns(first-1,last);
};
$('#geometry').onsubmit=e=>{
  e.preventDefault();const r=selected();if(!r)return;
  const [x,y,w,h]=keys.map(k=>fields[k].valueAsNumber);
  if(![x,y,w,h].every(Number.isInteger)||x<0||y<0||w<1||h<1||x+w>canvas.width||y+h>canvas.height){say('이미지 안에 들어오는 정수 좌표와 크기를 입력하세요.');return;}
  remember();Object.assign(r,{x,y,w,h});render();say(questionLabel(r.id)+' 좌표를 적용했습니다.');
};
$('#add').onclick=()=>{
  remember();drawing=false;
  const w=Math.max(1,Math.round(canvas.width/3)),h=Math.max(1,Math.round(canvas.height/3));
  const r={id:uid(),x:Math.floor((canvas.width-w)/2),y:Math.floor((canvas.height-h)/2),w,h};
  regions.push(r);active=r.id;render();fields.x.focus();fields.x.select();say(questionLabel(r.id)+' 영역을 추가했습니다. 좌표를 조절하세요.');
};
$('#close-file').onclick=()=>{
  documentRevision++;originalMargins.clear();reviewed=false;proposalCount=0;loadToken++;ready=false;drawing=false;drag=null;regions=[];history=[];active=null;
  canvas.width=canvas.height=1;$('#file-title').textContent='작업 공간';$('#image-size').textContent='';
  $('#resume')?.remove();overlay.replaceChildren();$('#list').replaceChildren();Object.values(fields).forEach(f=>f.value='');
  location.hash='home';route();$('.upload').focus();
};
$('#draw').onclick=()=>{drawing=!drawing;render();say(drawing?'이미지에서 원하는 영역을 드래그하세요.':'');};
$('#undo').onclick=()=>{const state=history.pop();if(state){reviewed=false;regions=state.regions;active=state.active;drawing=false;render();say('이전 편집으로 돌아갔습니다.');}};
$('#remove').onclick=()=>{const i=regions.findIndex(r=>r.id===active);if(i<0)return;remember();regions.splice(i,1);active=regions[Math.min(i,regions.length-1)]?.id??null;render();};
for(const [id,size,axis] of [['#split-h','h','y'],['#split-v','w','x']]){
  $(id).onclick=()=>{const r=selected();if(!r||r[size]<2)return;remember();const half=Math.floor(r[size]/2);
    const newRegion={...r,id:uid(),[axis]:r[axis]+half,[size]:r[size]-half};r[size]=half;
    regions.splice(regions.indexOf(r)+1,0,newRegion);render();};
}
const clamp=(n,lo,hi)=>Math.max(lo,Math.min(hi,n));
function point(e){
  const bounds=overlay.getBoundingClientRect();
  return {x:clamp(Math.round((e.clientX-bounds.left)/bounds.width*canvas.width),0,canvas.width),
          y:clamp(Math.round((e.clientY-bounds.top)/bounds.height*canvas.height),0,canvas.height)};
}
overlay.onpointerdown=e=>{
  if(e.button!==0 || drag)return;
  const target=e.target.closest('.region');if(!drawing&&!target)return;
  const start=point(e),state=capture();
  if(drawing){
    const rect={id:uid(),x:Math.min(start.x,canvas.width-1),y:Math.min(start.y,canvas.height-1),w:1,h:1};
    regions.push(rect);active=rect.id;
  }else active=target.dataset.id;
  drag={state,start,original:{...selected()},mode:drawing?'create':e.target.dataset.side||'move',pointer:e.pointerId};
  overlay.setPointerCapture(e.pointerId);e.preventDefault();paintBoxes();
};
overlay.onpointermove=e=>{
  if(!drag||drag.pointer!==e.pointerId)return;
  const p=point(e),{start,original:o,mode}=drag,r=selected(),dx=p.x-start.x,dy=p.y-start.y;
  if(mode==='create'){
    r.x=Math.min(Math.min(start.x,p.x),canvas.width-1);r.y=Math.min(Math.min(start.y,p.y),canvas.height-1);
    r.w=Math.max(1,Math.abs(dx));r.h=Math.max(1,Math.abs(dy));
  }else if(mode==='move'){
    r.x=clamp(o.x+dx,0,canvas.width-o.w);r.y=clamp(o.y+dy,0,canvas.height-o.h);
  }else{
    const x1=mode.endsWith('w')?clamp(o.x+dx,0,o.x+o.w-1):o.x;
    const y1=mode.startsWith('n')?clamp(o.y+dy,0,o.y+o.h-1):o.y;
    const x2=mode.endsWith('e')?clamp(o.x+o.w+dx,o.x+1,canvas.width):o.x+o.w;
    const y2=mode.startsWith('s')?clamp(o.y+o.h+dy,o.y+1,canvas.height):o.y+o.h;
    Object.assign(r,{x:x1,y:y1,w:x2-x1,h:y2-y1});
  }paintBoxes();
};
function finish(e,cancel){
  if(!drag||e.pointerId!==drag.pointer)return;
  if(cancel){regions=drag.state.regions;active=drag.state.active;}
  else if(drag.mode==='create'||keys.some(k=>selected()[k]!==drag.original[k]))remember(drag.state);
  drag=null;drawing=false;render();
}
overlay.onpointerup=e=>finish(e,false);overlay.onpointercancel=e=>finish(e,true);
$('#preview-export').onclick=()=>{
  if(exporting||!reviewed||!regions.length)return;
  const names=outputNames($('#file-title').textContent,regions.length);
  $('#export-preview-name').textContent=names.zip;
  $('#export-preview-entries').replaceChildren(...regions.map((r,i)=>{
    const li=document.createElement('li');
    li.textContent=names.png(i+1)+' · '+r.w+' × '+r.h+' px';
    return li;
  }));
  $('#export-preview-dialog').showModal();
};
async function exportFiles(all){
  if(exporting||!reviewed)return;
  const order=regions.map(r=>({...r})), chosen=all?order:order.filter(r=>r.id===active);
  if(!chosen.length)return;
  if(chosen.reduce((n,r)=>n+r.w*r.h,0)>64000000){say('한 번에 내보낼 수 있는 범위는 총 6,400만 픽셀입니다. 영역을 줄이거나 개별 PNG를 받으세요.');return;}
  const names=outputNames($('#file-title').textContent,order.length);
  const version=documentRevision;exporting=true;render();
  try{
    const files=[];let blob,name;const out=document.createElement('canvas');
    for(const r of chosen){
      if(version!==documentRevision)throw Error('이미지가 변경되어 다운로드를 중단했습니다.');
      out.width=r.w;out.height=r.h;out.getContext('2d').drawImage(canvas,r.x,r.y,r.w,r.h,0,0,r.w,r.h);
      blob=await new Promise((ok,fail)=>out.toBlob(b=>b?ok(b):fail(Error('PNG를 만들 수 없습니다.')),'image/png'));
      name=names.png(order.indexOf(r)+1);
      if(all)files.push({name,bytes:new Uint8Array(await blob.arrayBuffer())});
    }
    if(version!==documentRevision)throw Error('이미지가 변경되어 다운로드를 중단했습니다.');
    if(all){blob=zip(files);name=names.zip;}
    const href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=name;a.click();
    setTimeout(()=>URL.revokeObjectURL(href),30000);say(chosen.length+'개 영역의 다운로드를 시작했습니다.');
  }catch(e){say(e.message);}finally{exporting=false;render();}
}
$('#export-all').onclick=()=>exportFiles(true);$('#export-one').onclick=()=>exportFiles(false);
route();
