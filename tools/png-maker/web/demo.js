// Original classroom sample, drawn locally at source resolution.
export function worksheet(){
  const canvas=document.createElement('canvas');canvas.width=960;canvas.height=680;
  const c=canvas.getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,960,680);
  const tiles=[[40,38],[500,38],[40,354],[500,354]];
  const titles=['삼각형의 높이','좌표 읽기','대칭축 찾기','막대그래프 해석'];
  const prompts=['밑변에 수직인 선분을 찾아보세요.','점 A와 점 B의 좌표를 써 보세요.','도형을 반으로 나누는 선을 그어 보세요.','가장 큰 값과 작은 값의 차를 구하세요.'];
  tiles.forEach(([x,y],i)=>{
    c.save();c.translate(x,y);
    c.strokeStyle='#c4cbd2';c.lineWidth=2;c.strokeRect(1,1,418,284);
    c.fillStyle='#213342';c.font='bold 21px Malgun Gothic, sans-serif';c.fillText('0'+(i+1)+'   '+titles[i],22,37);
    c.font='15px Malgun Gothic, sans-serif';c.fillText(prompts[i],22,65);
    c.translate(90,90);c.lineWidth=2.6;c.strokeStyle='#345066';c.fillStyle='#d8e8f6';
    const line=(...points)=>{c.beginPath();c.moveTo(...points[0]);points.slice(1).forEach(p=>c.lineTo(...p));c.stroke();};
    if(i===0){
      c.beginPath();c.moveTo(10,138);c.lineTo(168,9);c.lineTo(224,138);c.closePath();c.fill();c.stroke();
      c.setLineDash([7,5]);line([168,9],[168,138]);c.setLineDash([]);line([158,138],[158,128],[168,128]);
      c.fillStyle='#345066';c.font='italic 15px Georgia';c.fillText('A',161,4);c.fillText('B',0,153);c.fillText('C',228,151);
    }else if(i===1){
      c.strokeStyle='#dce3e7';c.lineWidth=1;
      for(let k=0;k<9;k++)line([k*25,0],[k*25,150]);for(let k=0;k<7;k++)line([0,k*25],[200,k*25]);
      c.strokeStyle='#345066';c.lineWidth=2;line([0,125],[216,125]);line([50,152],[50,-4]);
      for(const [px,py,s] of [[100,50,'A'],[175,100,'B']]){c.fillStyle='#315b80';c.beginPath();c.arc(px,py,5,0,Math.PI*2);c.fill();c.font='italic 16px Georgia';c.fillText(s,px+8,py-5);}
    }else if(i===2){
      c.translate(108,72);c.beginPath();for(let j=0;j<6;j++){let a=Math.PI*j/3;c[j?'lineTo':'moveTo'](86*Math.cos(a),73*Math.sin(a));}c.closePath();c.fill();c.stroke();c.setLineDash([5,5]);line([0,-75],[0,76]);
    }else{
      [[18,72],[70,105],[122,49],[174,128]].forEach(([left,h],j)=>{c.fillStyle=['#cbd9e6','#9eb7cc','#bfcfdc','#7a9db7'][j];c.fillRect(left,145-h,31,h);c.strokeRect(left,145-h,31,h);});line([0,145],[225,145]);
    }
    c.restore();
  });return canvas;
}
