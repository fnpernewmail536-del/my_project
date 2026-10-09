/* Procedural pixel scene; no external images, videos or sounds. */
(()=>{
  'use strict';
  const colors=['#967042','#755231','#a78252','#638743','#86b04b'];
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  class BlockScene{
    constructor(canvas){this.canvas=canvas;this.ctx=canvas.getContext('2d');canvas.width=384;canvas.height=216;this.parts=[];this.hit=-1;this.last=0;this.exploded=false;}
    polygon(points,color){const c=this.ctx;c.fillStyle=color;c.beginPath();points.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));c.closePath();c.fill();}
    block(x,y,size=44,tnt=false,crack=0){
      const c=this.ctx,w=size,h=size*.55;
      this.polygon([[x,y-h],[x+w,y-h*1.5],[x+w*2,y-h],[x+w,y-h*.5]],tnt?'#ed6b4b':'#86b24d');
      this.polygon([[x,y-h],[x+w,y-h*.5],[x+w,y+size],[x,y+size-h*.5]],tnt?'#b53e31':'#846041');
      this.polygon([[x+w,y-h*.5],[x+w*2,y-h],[x+w*2,y+size-h*.5],[x+w,y+size]],tnt?'#8c2b27':'#61452f');
      c.save();c.beginPath();c.moveTo(x,y-h);c.lineTo(x+w,y-h*.5);c.lineTo(x+w,y+size);c.lineTo(x,y+size-h*.5);c.clip();
      for(let row=0;row<14;row++)for(let col=0;col<12;col++){
        const seed=(row*37+col*19)%13;c.fillStyle=tnt?(seed%2?'#c04733':'#9f3429'):colors[seed%3];
        c.fillRect(x+col*5,y-h+row*5,seed%3+2,seed%4+2);
      }
      if(!tnt){c.fillStyle='#709841';c.fillRect(x,y-h,2*w,9);c.fillStyle='#4d7535';c.fillRect(x+5,y-h+9,10,5);c.fillRect(x+27,y-h+9,10,8);}
      else{c.fillStyle='#ece6c7';c.fillRect(x,y+3,w,16);c.fillStyle='#302d26';c.font='bold 10px monospace';c.fillText('TNT',x+7,y+15);}
      c.restore();
      if(tnt){this.polygon([[x+w,y+3],[x+w*2,y-9],[x+w*2,y+7],[x+w,y+19]],'#bcb89b');c.fillStyle='#2e2520';c.fillRect(x+w,y-h*.65-8,3,10);}
      if(crack>0){
        c.strokeStyle='#181a13';c.lineWidth=2;c.beginPath();
        const paths=[[[x+17,y-6],[x+23,y+5],[x+15,y+17]],[[x+23,y+5],[x+35,y+11],[x+27,y+29]],[[x+15,y+17],[x+4,y+24]],[[x+35,y+11],[x+42,y-2]],[[x+27,y+29],[x+38,y+37]]];
        for(const path of paths.slice(0,Math.ceil(crack*paths.length))){c.moveTo(...path[0]);path.slice(1).forEach(p=>c.lineTo(...p));}c.stroke();
      }
    }
    creeper(x,y,t,charge=false){
      const c=this.ctx;const wobble=Math.round(Math.sin(t*4)*1.5);x+=wobble;
      this.polygon([[x+25,y-39],[x+37,y-44],[x+37,y-15],[x+25,y-9]],'#3d622c');
      c.fillStyle=charge&&Math.sin(t*5)>0?'#b4c76d':'#679646';c.fillRect(x,y-39,26,30);
      for(let i=0;i<16;i++){c.fillStyle=i%2?'#486f35':'#87b754';c.fillRect(x+(i*17)%25,y-38+(i*11)%28,4,4);}
      c.fillStyle='#192a1c';c.fillRect(x+4,y-30,7,7);c.fillRect(x+17,y-30,7,7);c.fillRect(x+11,y-23,6,5);c.fillRect(x+7,y-19,14,5);c.fillRect(x+7,y-15,5,5);c.fillRect(x+17,y-15,5,5);
      c.fillStyle='#497136';c.fillRect(x+6,y-9,17,24);c.fillStyle='#344f2a';c.fillRect(x+6,y+10,8,8);c.fillRect(x+18,y+10,9,8);
      c.fillStyle='#80a653';c.fillRect(x+8,y-7,4,8);c.fillRect(x+18,y+1,3,10);
    }
    tool(x,y,angle){
      const c=this.ctx;c.save();c.translate(x,y);c.rotate(angle);
      c.fillStyle='#352719';c.fillRect(-4,-57,10,68);c.fillStyle='#b18549';c.fillRect(-2,-55,5,64);
      c.fillStyle='#16484a';c.fillRect(-27,-64,51,10);c.fillRect(-28,-58,10,8);c.fillRect(17,-58,10,15);
      c.fillStyle='#65d6cf';c.fillRect(-25,-63,48,5);c.fillRect(19,-58,5,13);c.fillRect(-27,-56,5,5);
      c.fillStyle='#a66e4c';c.fillRect(-8,-7,17,19);c.fillStyle='#ce9970';c.fillRect(-7,-7,15,8);c.fillStyle='#374c60';c.fillRect(-10,12,23,36);c.restore();
    }
    fragments(x,y,n,explosion=false){
      for(let i=0;i<n&&this.parts.length<72;i++){
        const a=i*2.399+Math.random()*.25,s=explosion?35+Math.random()*80:12+Math.random()*30;
        this.parts.push({x,y,vx:Math.cos(a)*s,vy:Math.sin(a)*s-(explosion?40:18),life:explosion?1.6: .65,size:explosion?3+Math.random()*5:2+Math.random()*3,color:colors[i%colors.length]});
      }
    }
    draw(ms,{phase='running',effect='auto',reduced=false,finishedMs=null,available=true}={}){
      const c=this.ctx;if(!c)return;
      const t=ms/1000,dt=clamp((ms-this.last)/1000,0,.05);this.last=ms;
      const cycle=available&&!reduced?t%3:1.2,hit=Math.floor(cycle/.6);
      const done=phase==='done',failed=['error','unknown'].includes(phase),boom=done&&effect!=='mine';
      const finishTime=finishedMs===null?99:Math.max(0,(ms-finishedMs)/1000);
      c.imageSmoothingEnabled=false;c.clearRect(0,0,384,216);
      c.fillStyle='#15252b';c.fillRect(0,0,384,216);
      for(let i=0;i<32;i++){c.fillStyle=i%3?'#52645c':'#84977b';c.fillRect((i*67+19)%384,(i*23+11)%79,i%4===0?2:1,1);}
      this.polygon([[0,113],[49,64],[88,105],[128,81],[181,130],[224,86],[279,116],[338,62],[384,110],[384,180],[0,180]],'#223b38');
      this.polygon([[0,139],[63,113],[91,131],[158,104],[202,143],[288,113],[333,126],[384,115],[384,180],[0,180]],'#325045');
      c.fillStyle='#547b39';c.fillRect(0,165,384,5);c.fillStyle='#293d2b';c.fillRect(0,170,384,46);
      for(let i=0;i<50;i++){c.fillStyle=i%3?'#385135':'#56733f';c.fillRect(i*13%384,172+i*19%42,4,2);}
      c.fillStyle='#10201e';c.fillRect(110,162,190,5);
      const x=132,y=121;
      if(!done){
        const shake=!reduced&&available&&cycle%.6>.38?Math.round(Math.sin(cycle*70)*2):0;
        this.block(x+shake,y,40,false,failed?.3:hit/4);
        if(!failed){
          const swing=reduced?-.7:-1.05+Math.max(0,Math.sin(cycle/.6*Math.PI*2))*.92;
          this.tool(240,143,swing);
          if(hit!==this.hit&&available&&!reduced){this.fragments(x+35,y-3,8);this.hit=hit;}
        }
      }else if(reduced){
        c.fillStyle='#8eb566';c.fillRect(161,149,9,9);c.fillStyle='#c2d2a5';c.fillRect(164,149,4,3);
      }else if(finishTime<.18){this.block(x,y,40,effect==='tnt',1);}
      const sceneTime=available&&!reduced?t:0;
      const actor=effect==='auto'?(Math.floor(sceneTime/6)%2?'creeper':'tnt'):effect;
      if((!done||finishTime<.18)&&!failed&&actor==='tnt'){
        this.block(284,148,20,true,0);
        if(!reduced&&available){c.fillStyle='#efbf60';c.fillRect(303,130,3,3);c.fillStyle='#768477';c.fillRect(300,122-Math.floor(t*8)%6,3,3);}
      }
      if((!done||finishTime<.18)&&!failed&&actor==='creeper')this.creeper(294,149,sceneTime,!reduced&&available);
      if(done&&!reduced&&!this.exploded&&finishTime<2){this.fragments(boom?304:x+42,boom?143:y,boom?46:34,boom);if(boom)this.fragments(x+42,y,16,true);this.exploded=true;}
      if(boom&&!reduced&&finishTime<1.7){
        const size=Math.floor(finishTime*100);c.save();c.globalAlpha=Math.max(0,1-finishTime/1.7);
        for(let i=0;i<7;i++){const px=304+Math.cos(i*2.4)*size,py=143+Math.sin(i*2.4)*size*.6-finishTime*20;c.fillStyle=i%2?'#a5aea2':'#69756d';c.fillRect(Math.round(px-12),Math.round(py-12),24,24);}
        c.strokeStyle='#d1d9b1';c.lineWidth=3;c.strokeRect(304-size,143-size*.55,size*2,size*1.1);c.restore();
      }
      if(reduced)this.parts=[];
      this.parts=this.parts.filter(p=>p.life>0);
      for(const p of this.parts){if(!reduced&&available){p.x+=p.vx*dt;p.y+=p.vy*dt;p.vy+=95*dt;p.life-=dt;}c.globalAlpha=clamp(p.life*1.8,0,1);c.fillStyle=p.color;c.fillRect(Math.round(p.x),Math.round(p.y),p.size,p.size);}c.globalAlpha=1;
      c.fillStyle='#102322';c.fillRect(10,11,78,15);c.fillStyle='#a5bf88';c.font='9px monospace';c.fillText(done?'WORLD / CLEAR':'WORLD / ACTIVE',15,22);
      if(!done&&actor!=='mine'){c.fillStyle='#182e28';c.fillRect(266,184,99,16);c.fillStyle='#bbc7a4';c.fillText(actor==='creeper'?'CREEPER STANDBY':'TNT STANDBY',270,195);}
      this.canvas.dataset.event=done?(boom?'explosion':'broken'):failed?'stopped':'mining';this.canvas.dataset.hit=String(hit);
    }
  }
  window.CatPSBlockScene=BlockScene;
})();
