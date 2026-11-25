// Foreground projection with transparent-border and paper-border modes.
export function propose({data,width,height}) {
  const samples = [[], [], [], []];
  function sample(x,y) {
    const i = (y*width+x)*4;
    samples.forEach((values,k)=>values.push(data[i+k]));
  }
  for (let x=0; x<width; x+=Math.max(1,Math.floor(width/200))) {sample(x,0); sample(x,height-1);}
  for (let y=0; y<height; y+=Math.max(1,Math.floor(height/200))) {sample(0,y); sample(width-1,y);}
  const bg=samples.map(a=>a.sort((p,q)=>p-q)[Math.floor(a.length/2)]);
  const mask=new Uint8Array(width*height);
  const rootCols=new Uint8Array(width),rootRows=new Uint8Array(height);
  const rgba=new Uint32Array(data.buffer,data.byteOffset,mask.length);
  const background=new Uint32Array(Uint8Array.from(bg).buffer)[0];
  const transparent=bg[3]<24;
  for(let y=0;y<height;y++) {
    const base=y*width;
    for(let x=0;x<width;) {
      const first=x,i=base+x,p=i*4,color=rgba[i],alpha=data[p+3];
      // Worksheet pages often contain long flat runs of paper, ink, or transparency.
      do{x++;}while(x<width && rgba[base+x]===color);
      if(alpha<16 || (!transparent && color===background))continue;
      if(!transparent){
        const d=Math.max(Math.abs(data[p]-bg[0]),Math.abs(data[p+1]-bg[1]),Math.abs(data[p+2]-bg[2]));
        if(d*alpha/255<=24)continue;
      }
      rootRows[y]=1;
      if(x-first===1){mask[i]=1;rootCols[first]=1;}
      else {mask.fill(1,i,base+x);rootCols.fill(1,first,x);}
    }
  }
  const rects=[];
  const minX=Math.max(7,Math.round(width*.018)), minY=Math.max(6,Math.round(height*.014));
  function bands(counts, gap) {
    const result=[];let first=-1,last=-1;
    for(let i=0;i<counts.length;i++) {
      if(!counts[i])continue;
      if(first<0)first=i;
      else if(i-last-1>=gap){result.push([first,last+1]);first=i;}
      last=i;
    }
    if(first>=0)result.push([first,last+1]);
    return result;
  }
  // Only split a narrow gap when both pieces have a closed frame along all four edges.
  function framed(x,y,w,h){
    if(w<100 || h<32)return null;
    let left=w,right=-1;
    const scanline=j=>mask.subarray((y+j)*width+x,(y+j)*width+x+w);
    for(let j=0;j<h;j++){
      const row=scanline(j),a=row.indexOf(1),b=row.lastIndexOf(1);
      if(a>=0){left=Math.min(left,a);right=Math.max(right,b);}
    }
    const span=right-left+1;
    if(span<w*.8)return null;
    const horizontal=j=>{
      const row=scanline(j);let ink=0;
      for(let k=left;k<=right;k++)ink+=row[k];
      return ink>=span*.97;
    };
    if(![0,1,2].some(j=>horizontal(j))||![h-1,h-2,h-3].some(j=>horizontal(j)))return null;
    let hasPaper=false;
    for(let n=1;n<=16;n++){
      const row=scanline(Math.floor(h*n/17));
      if(!row.subarray(left,left+4).includes(1)||!row.subarray(right-3,right+1).includes(1))return null;
      if(!row[left+Math.floor(span/2)])hasPaper=true;
    }
    return hasPaper?{x:x+left,y,w:span,h}:null;
  }
  let truncated=false;
  function cut(x,y,w,h,depth=0) {
    if(rects.length>=256){truncated=true;return;}
    const cols=depth?new Uint8Array(w):rootCols,rows=depth?new Uint8Array(h):rootRows;
    if(depth)for(let j=0;j<h;j++)for(let i=0;i<w;i++)if(mask[(y+j)*width+x+i]){rows[j]=1;cols[i]=1;}
    const xb=bands(cols,minX),yb=bands(rows,minY);
    if(!xb.length)return;
    const left=xb[0][0],right=xb.at(-1)[1],top=yb[0][0],bottom=yb.at(-1)[1];
    if(depth<12 && xb.length>1){for(const [a,b] of xb)cut(x+a,y+top,b-a,bottom-top,depth+1);return;}
    if(depth<12 && yb.length>1){for(const [a,b] of yb)cut(x+left,y+a,right-left,b-a,depth+1);return;}
    if(right-left>=100 && bottom-top>=66){
      const pair=bands(rows,2);
      if(pair.length===2 && rects.length<=254){
        const frames=pair.map(([a,b])=>framed(x+left,y+a,right-left,b-a));
        if(frames.every(Boolean)){rects.push(...frames);return;}
      }
    }
    rects.push({x:x+left,y:y+top,w:right-left,h:bottom-top});
  }
  cut(0,0,width,height);
  const lines=[];
  for(const rect of rects.sort((a,b)=>a.y-b.y || a.x-b.x)) {
    const line=lines.find(l=>Math.min(l.bottom,rect.y+rect.h)-Math.max(l.top,rect.y)>=Math.min(l.h,rect.h)*.45);
    if(line)line.items.push(rect);
    else lines.push({top:rect.y,bottom:rect.y+rect.h,h:rect.h,items:[rect]});
  }
  // Only suggest margin review around isolated printed rules on portrait pages.
  // The source and the proposed crops stay intact until the user chooses to exclude them.
  let topRule=null,bottomRule=null;
  if(height>=width && height>=400 && !transparent && rects.length>=4 && !truncated){
    for(const [start,end] of bands(rootRows,1)){
      if(end-start>Math.max(4,Math.round(height*.003)))continue;
      const nearTop=end<=height*.14,nearBottom=start>=height*.86;
      if(!nearTop && !nearBottom)continue;
      const row=mask.subarray(Math.floor((start+end-1)/2)*width,(Math.floor((start+end-1)/2)+1)*width);
      const left=row.indexOf(1),right=row.lastIndexOf(1);
      if(left<0 || right-left+1<width*.8 || row.subarray(left,right+1).includes(0))continue;
      if(nearTop && topRule===null)topRule=end;
      if(nearBottom)bottomRule=start;
    }
  }
  const inMargin=r=>(topRule!==null && r.y+r.h<=topRule)||(bottomRule!==null && r.y>=bottomRule);
  const margins=rects.filter(inMargin);
  if(rects.length-margins.length<3)margins.length=0;
  return {rects:lines.flatMap(l=>l.items.sort((a,b)=>a.x-b.x)),truncated,margins};
}

const crcTable=Uint32Array.from({length:256},(_,value)=>{
  for(let j=0;j<8;j++)value=(value>>>1)^((value&1)?0xedb88320:0);
  return value>>>0;
});
function checksum(bytes) {
  let crc=0xffffffff;
  for(let i=0,n=bytes.length;i<n;i++)crc=crcTable[(crc^bytes[i])&255]^(crc>>>8);
  return (crc^0xffffffff)>>>0;
}
// Stored UTF-8 entries, fixed DOS date, no platform timestamps.
export function zip(files) {
  if(!Array.isArray(files)||files.length>0xffff)throw Error('ZIP entry limit exceeded.');
  const encoder=new TextEncoder();
  let expectedOffset=0,expectedDirSize=0;
  const entries=files.map(({name,bytes})=>{
    if(typeof name!=='string'||!/^[^\/\\\u0000]+$/u.test(name)||name==='.'||name==='..'||!(bytes instanceof Uint8Array))throw Error('Invalid ZIP entry.');
    const label=encoder.encode(name);
    if(label.length>0xffff||bytes.length>0xffffffff)throw Error('ZIP entry limit exceeded.');
    expectedOffset+=30+label.length+bytes.length;expectedDirSize+=46+label.length;
    if(expectedOffset+expectedDirSize+22>0xffffffff)throw Error('ZIP size limit exceeded.');
    return {label,bytes};
  });
  const content=[], directory=[];
  let offset=0,dirSize=0;
  for(const {label,bytes} of entries) {
    const crc=checksum(bytes);
    function header(central) {
      const prefix=central?46:30,arr=new Uint8Array(prefix+label.length),d=new DataView(arr.buffer);
      const n16=(i,v)=>d.setUint16(i,v,true), n32=(i,v)=>d.setUint32(i,v,true);
      n32(0,central?0x02014b50:0x04034b50);
      const z=central?2:0;
      if(central)n16(4,20);
      n16(4+z,20);n16(6+z,0x0800);n16(12+z,33);
      n32(14+z,crc);n32(18+z,bytes.length);n32(22+z,bytes.length);n16(26+z,label.length);
      if(central)n32(42,offset);
      arr.set(label,prefix);return arr;
    }
    const local=header(false),central=header(true);
    content.push(local,bytes);directory.push(central);dirSize+=central.length;offset+=local.length+bytes.length;
  }
  const end=new Uint8Array(22),view=new DataView(end.buffer);
  view.setUint32(0,0x06054b50,true);view.setUint16(8,files.length,true);view.setUint16(10,files.length,true);
  view.setUint32(12,dirSize,true);view.setUint32(16,offset,true);
  return new Blob([...content,...directory,end],{type:'application/zip'});
}
