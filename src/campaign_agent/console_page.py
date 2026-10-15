"""The page of the Campaign Agent window: a small pixel-art office in the style of the old handheld role-playing games.

The CEO (the person at the keyboard) types a campaign. The manager (the Campaign Agent) reads it, asks questions, writes
the leadgen file and, once the CEO approves, sends it down the cable to the employee (the Marketing Agent), who comes in,
sits at the desk and works. Blockers and "task complete" walk back to the manager. Everything comes from the real
Campaign Agent and the real Marketing Agent; only the demo is scripted, and it is tagged SIMULATED on every line.
No images or fonts are loaded: the sprites are drawn with rectangles.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Campaign Agent</title>
<style>
:root{--ink:#181425;--paper:#f8f4e8;--sim:#8a4fd1;--ok:#3fbf6a;--warn:#e8a317;--fail:#e0443e;--off:#8b93a1}
*{box-sizing:border-box;scrollbar-width:none}
*::-webkit-scrollbar{display:none}
html,body{-ms-overflow-style:none}
body{margin:0;background:#0d0b14;color:var(--paper);font:700 15px/1.3 "Courier New",monospace;display:flex;flex-direction:column;align-items:center;min-height:100vh}
#top{width:min(100%,1500px);display:flex;gap:10px;align-items:center;padding:10px 12px}
#top b{flex:1;letter-spacing:.08em}
.pill{padding:2px 10px;border:2px solid var(--paper);border-radius:4px;font-size:12px}
.pill.live{color:var(--ok);border-color:var(--ok)}.pill.sim{background:var(--sim);border-color:var(--sim);color:#fff}
button{font:inherit;font-size:13px;background:var(--paper);color:var(--ink);border:2px solid var(--ink);border-radius:4px;padding:5px 12px;cursor:pointer;box-shadow:2px 2px 0 #000}
button:disabled{opacity:.45;cursor:default}button.go{background:#ffd84a}button.no{background:#f4b1ab}
#layout{width:min(100%,1500px);display:flex;gap:14px;align-items:flex-start;padding:0 12px}
#main{flex:1 1 0;min-width:0}
#side{flex:0 0 330px;position:sticky;top:8px;max-height:calc(100vh - 16px);overflow:auto;border:2px solid var(--paper);border-radius:8px;background:#1a1626;padding:12px}
#side h3{margin:14px 0 6px;font-size:12px;letter-spacing:.08em;color:#ffd84a}#side h3:first-child{margin-top:0}
#side pre{max-height:none;margin:0;font-size:11px}
@media(max-width:900px){#layout{flex-direction:column}#side{flex:none;width:100%;position:static;max-height:none}}
canvas{width:100%;display:block;image-rendering:pixelated;border:4px solid var(--paper);border-radius:6px;background:#000}
#box{position:relative;margin-top:10px;background:var(--paper);color:var(--ink);border:4px solid var(--ink);outline:4px solid var(--paper);border-radius:8px;padding:16px 18px 14px;min-height:92px;font-size:clamp(14px,2.4vw,19px)}
#who{position:absolute;top:-16px;left:14px;background:var(--ink);color:var(--paper);padding:2px 10px;border-radius:4px;font-size:13px}
#arrow{position:absolute;right:14px;bottom:8px;animation:b .8s steps(2) infinite}
@keyframes b{50%{opacity:0}}
#ceo{margin:18px 0 0;padding:12px;border:2px solid var(--paper);border-radius:8px;background:#1a1626}
#ceo h3{margin:0 0 8px;font-size:12px;letter-spacing:.08em;color:#ffd84a}
textarea{width:100%;min-height:62px;font:inherit;font-size:14px;padding:8px;border:2px solid var(--ink);border-radius:4px;background:var(--paper);color:var(--ink);resize:vertical}
.row2{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center}
.q{margin:10px 0;padding:8px;border:1px dashed #5b5478;border-radius:4px}.q p{margin:0 0 6px}
.ch{margin:2px 4px 2px 0;font-size:12px;padding:3px 8px}.ch.on{background:#ffd84a}
.q input{font:inherit;font-size:13px;padding:4px;width:min(100%,360px);border:2px solid var(--ink);border-radius:4px}
pre{white-space:pre-wrap;word-break:break-word;font:12px/1.4 ui-monospace,monospace;background:#0d0b14;border:1px solid #3a3452;padding:8px;max-height:220px;overflow:auto;margin:8px 0 0}
#note{color:#b9b3cf;font-weight:400;font-size:12px;margin-top:6px}
#panel .row:first-child{border-top:0}
.row{display:flex;gap:10px;padding:5px 0;border-top:1px solid #2c2740}
.dot{width:12px;height:12px;margin-top:3px;flex:none;border:2px solid #000}
.ok{background:var(--ok)}.warn{background:var(--warn)}.fail{background:var(--fail)}.off{background:var(--off)}
.nm{width:112px;flex:none}.dt{color:#b9b3cf;font-weight:400;word-break:break-word}
#help{margin:18px 0 24px;color:#8b87a3;font-weight:400;font-size:12px}
</style></head><body>
<div id="top"><b>FTECHIZ SOLUTIONS OFFICE</b><span id="mode" class="pill live">LIVE</span><button id="bp" title="What the dashboard buttons will do: asks the Campaign Agent service to pause or resume">Pause CA</button><button id="bd">Demo (D)</button></div>
<div id="layout"><div id="main">
<div id="stage"><canvas id="g" width="320" height="200"></canvas>
<div id="box"><span id="who"></span><span id="txt"></span><span id="arrow">&#9660;</span></div></div>
<section id="ceo"><h3>TEST INPUT: you are the CEO. Later the dashboard writes the campaign text to the database and the manager reads it from there.</h3>
<div id="compose"><textarea id="msg" maxlength="2000" placeholder="Type the campaign details here, in plain words, as the dashboard user would."></textarea>
<div class="row2"><button id="send" class="go">Send to the manager</button><button id="preset">Use yesterday&#39;s test campaign</button><span id="note"></span></div></div>
<div id="qs"></div><div id="act" class="row2"></div><pre id="yaml" hidden></pre></section>
<div id="help">CEO = you (a test stand-in for the dashboard, which will supply the campaign text from the database). Manager = Campaign Agent. Employee = Marketing Agent. The office shows the real state, refreshed every few seconds. Space speeds up the text. F11 leaves or re-enters full screen. Alt+F4 closes the window. Emails always need your approval in Slack and only go to the test address.</div>
</div>
<aside id="side"><h3>WHAT IS WORKING NOW</h3><div id="panel"></div>
<h3>RECENT REQUESTS</h3><pre id="reqs">none</pre>
<h3>MARKETING AGENT EVENTS</h3><pre id="evs">none</pre></aside>
</div>
<script>
"use strict";
const $=id=>document.getElementById(id), W=320, H=200, cv=$('g'), c=cv.getContext('2d');
const COL={ok:'#3fbf6a',warn:'#e8a317',fail:'#e0443e',off:'#8b93a1'};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const SIM={on:false};
let tick=0, data=null;
// ---------------- actors ----------------
const mk=(x,y,o)=>Object.assign({x,y,path:[],done:null,face:1,sit:false,vis:true,bubble:'',bcol:'#f8f4e8',mood:''},o||{});
const mgr=mk(62,110,{sit:true}), ceo=mk(-30,130,{vis:false}), emp=mk(352,130,{vis:false,face:-1});
const SEAT_M=[62,110], SEAT_E=[252,110], TALK=[100,122], TALK_E=[88,122];
function go(a,pts){return new Promise(res=>{a.sit=false;a.path=pts.map(p=>p.slice());a.done=res;if(!pts.length){a.done=null;res()}})}
function step(a){
  if(!a.path.length)return;const [tx,ty]=a.path[0],dx=tx-a.x,dy=ty-a.y,d=Math.hypot(dx,dy);
  if(d<1.3){a.x=tx;a.y=ty;a.path.shift();if(!a.path.length&&a.done){const f=a.done;a.done=null;f()}return}
  a.x+=dx/d*1.3;a.y+=dy/d*1.3;if(Math.abs(dx)>.4)a.face=dx>0?1:-1;
}
const R=(x,y,w,h,col)=>{c.fillStyle=col;c.fillRect(Math.round(x),Math.round(y),w,h)};
function person(a,suit,hair,tie,opt){
  if(!a.vis)return;opt=opt||{};
  const moving=a.path.length>0,ph=Math.floor(tick/8)%2,bob=moving?ph:(opt.work?Math.floor(tick/14)%2:0);
  const x=Math.round(a.x)-6,y=Math.round(a.y)-26+bob;
  R(x+1,y+27,10,2,'rgba(0,0,0,.25)');
  if(a.sit){R(x-1,y+8,14,18,'#6b4a2a');R(x+1,y+10,10,16,'#7d5a35')}                         // chair
  else{ // two legs of the same length; walking moves them up and down together-opposite, never shortens one
    const l=moving?(ph?-1:0):0,r=moving?(ph?0:-1):0;R(x+2,y+20+l,3,7,'#2a2438');R(x+7,y+20+r,3,7,'#2a2438')}
  if(a.sit)R(x+1,y+21,10,3,'#3a2c4a');                                                      // lap, legs hidden by the desk
  R(x+1,y+10,10,11,suit);R(x,y+11,2,a.sit?6:8,suit);R(x+10,y+11,2,a.sit?6:8,suit);
  if(tie)R(x+5,y+11,2,7,tie);
  R(x+2,y+1,8,9,'#f0c8a0');R(x+1,y,10,4,hair);R(x+1,y+3,2,4,hair);R(x+9,y+3,2,4,hair);
  if(opt.hat){R(x+2,y-3,8,3,opt.hat);R(x+1,y,10,1,opt.hat)}
  if(opt.sleep){R(x+3,y+6,2,1,'#000');R(x+7,y+6,2,1,'#000')}else{R(x+3,y+5,2,2,'#000');R(x+7,y+5,2,2,'#000')}
  if(opt.sad)R(x+4,y+8,4,1,'#8a2b2b');else R(x+4,y+8,4,1,'#8a4b3a');
  if(a.bubble)bubble(a);
}
function bubble(a){
  c.font='bold 8px "Courier New",monospace';const w=Math.ceil(c.measureText(a.bubble).width)+8,x=Math.round(a.x-w/2),y=Math.round(a.y-46);
  R(x-1,y-1,w+2,14,'#181425');R(x,y,w,12,a.bcol);R(Math.round(a.x)-2,y+12,4,3,'#181425');R(Math.round(a.x)-1,y+11,2,3,a.bcol);
  c.fillStyle='#181425';c.textBaseline='top';c.fillText(a.bubble,x+4,y+2);
}
// ---------------- the office ----------------
function floor(x0,x1,a,b){for(let y=48;y<H;y+=16)for(let x=x0;x<x1;x+=16)R(x,y,16,16,((x+y)/16)%2?a:b)}
function monitor(x,y,state,t){
  R(x,y,26,18,'#181425');R(x+2,y+2,22,12,state==='ok'?'#143d2a':state==='fail'?'#2a1212':'#22222c');
  if(state==='ok')for(let i=0;i<4;i++){const w=6+((i*7+Math.floor(t/10))%14);R(x+3,y+3+i*3,w,1,'#7dffa8')}
  else if(state==='fail'){R(x+9,y+4,2,7,'#e0443e');R(x+9,y+13,2,1,'#e0443e')}
  R(x+10,y+18,6,3,'#444');
}
const desk=(x,y,w)=>{R(x,y,w,24,'#9a6b3f');R(x,y,w,3,'#c08a55');R(x,y+24,w,3,'#6e4a28')};
function cabinet(x,y,state){R(x,y,22,30,'#8f98a8');R(x,y,22,3,'#b4bccb');for(let i=0;i<3;i++){R(x+2,y+4+i*8,18,7,'#a9b2c2');R(x+8,y+6+i*8,6,2,'#5b6372')}R(x+17,y+1,3,2,COL[state]||COL.off)}
const lamp=(x,y,on)=>{R(x,y+6,2,6,'#555');R(x-3,y,8,6,on?'#ffe066':'#4a4a55');if(on)R(x-5,y+6,12,1,'rgba(255,224,102,.25)')};
function paper(x,y,lines,col){R(x,y,13,16,'#fff');R(x,y,13,1,'#cfcfcf');for(let i=0;i<lines;i++)R(x+2,y+3+i*3,9-(i%3)*2,1,col||'#3b6fd6')}
function letters(x,y,n){const k=Math.min(n,6);for(let i=0;i<k;i++){R(x+(i%3)*6,y-Math.floor(i/3)*4,5,4,'#fff');R(x+(i%3)*6+3,y-Math.floor(i/3)*4,2,2,'#c0392b')}}
function door(x,y,side){R(x,y,6,38,'#5a3b22');R(x+(side<0?1:0),y+2,5,34,'#7a5230');R(x+(side<0?3:1),y+19,2,2,'#ffd84a')}
const CABLE=[[62,126],[62,176],[258,176],[258,126]];
const CLEN=48*2+196;
function cablePt(d){d=Math.max(0,Math.min(CLEN,d));return d<50?[62,126+d]:d<50+196?[62+d-50,176]:[258,176-(d-246)]}
function cable(state,t,busy){
  const col=COL[state==='ok'?'ok':state]||COL.off;
  c.lineWidth=3;c.strokeStyle='#181425';c.beginPath();CABLE.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));c.stroke();
  c.lineWidth=1;c.strokeStyle=state==='fail'?'#7a2b28':state==='off'?'#555':col;c.setLineDash(state==='off'?[3,3]:[]);c.stroke();c.setLineDash([]);
  if(state==='fail'){R(154,172,4,8,'#000');R(163,172,4,8,'#000');if(Math.floor(t/6)%2){R(157,168,2,2,'#ffe066');R(161,181,2,2,'#ffe066')}return}
  if(state==='off')return;
  const n=state==='ok'?4:2,sp=busy?1.6:.6;
  for(let i=0;i<n;i++){const p=cablePt((t*sp+i*CLEN/n)%CLEN);R(p[0]-1,p[1]-1,3,3,'#fff')}
}
// ---------------- what the real world says ----------------
const S={paused:false,worker:'off',model:'off',db:'off',link:'off',ma:'off',waiting:0,running:0,blockers:0,maWorking:false};
const F={id:null,notes:[],state:'NEW',busy:false,live:false,q:[],cand:null,failed:null,shown:'',yaml:false,pipe:null};
const scene_={thinking:false,fileOnDesk:false,token:null,maWorking:false,maAlert:false,maOffer:false};
function readState(){
  const g=n=>{const l=data.lines.find(x=>x.name===n);return l?l.state:'off'};
  S.worker=g('Worker');S.paused=!!data.lines.find(x=>x.name==='Worker'&&/^paused/.test(x.detail));S.model=g('Model server');S.db=g('Database');S.link=g('Link to MA');S.ma=g('Marketing Agent');
  const q=data.lines.find(x=>x.name==='Requests'),m=q&&q.detail.match(/(\d+) waiting, (\d+) running/);S.waiting=m?+m[1]:0;S.running=m?+m[2]:0;
  const ma=data.lines.find(x=>x.name==='Marketing Agent'),b=ma&&ma.detail.match(/(\d+) open blocker/);S.blockers=b?+b[1]:0;S.maWorking=!!(ma&&/running a pass/.test(ma.detail));
}
// ---------------- the scene ----------------
function draw(){
  const t=tick,sim=SIM.on;
  R(0,0,W,H,'#2b2540');
  floor(0,148,'#cdb88c','#c1ab7e');floor(172,W,'#aac7b4','#9dbba8');floor(148,172,'#8d8f99','#848691');
  R(0,0,W,48,'#5d4f82');R(0,40,W,8,'#41375e');R(148,0,24,H,'#41375e');R(148,100,24,50,'#8d8f99');
  door(0,102,-1);door(314,102,1);
  c.fillStyle='#f8f4e8';c.font='bold 8px "Courier New",monospace';c.textBaseline='top';c.fillText('CAMPAIGN OFFICE',8,8);c.fillText('MARKETING FLOOR',182,8);
  R(40,14,30,20,'#9fd3ff');R(54,14,2,20,'#41375e');R(222,14,30,20,'#9fd3ff');R(236,14,2,20,'#41375e');
  const mgrAsleep=!sim&&(S.worker==='fail'||S.worker==='off');
  mgr.bubble=!sim&&S.paused?'PAUSED':(mgr.bubble==='PAUSED'?'':mgr.bubble);mgr.bcol='#ffd84a';
  const jobsBusy=!sim&&S.running>0&&!S.paused;   // a request is being worked on (from the dashboard or from this window)
  if(jobsBusy&&mgr.bubble==='')mgr.bubble='...';else if(!jobsBusy&&mgr.bubble==='...'&&!scene_.thinking)mgr.bubble='';
  desk(24,70,76);monitor(36,52,!sim&&S.model==='fail'?'fail':(mgrAsleep||(!sim&&S.paused)?'off':'ok'),t);monitor(66,52,mgrAsleep||(!sim&&S.paused)?'off':'ok',t+30);
  cabinet(118,52,sim?'ok':S.db);lamp(18,50,sim||S.model==='ok');
  if(S.waiting&&!sim)letters(30,90,S.waiting);
  if(scene_.thinking||jobsBusy)paper(80,76,1+Math.floor(t/12)%5);else if(scene_.fileOnDesk)paper(80,76,5);
  desk(208,70,76);monitor(222,52,scene_.maWorking?'ok':'off',t);if(scene_.maWorking&&emp.sit){paper(262,76,3,'#2f9e5b')}
  cable(sim?'ok':S.link,t,scene_.maWorking||!!scene_.token);
  c.fillStyle='#f8f4e8';c.fillText('TAILSCALE',134,184);
  if(scene_.token){const p=cablePt(scene_.token.d);R(p[0]-6,p[1]-8,13,16,'#fff');R(p[0]-4,p[1]-5,9,1,'#3b6fd6');R(p[0]-4,p[1]-2,7,1,'#3b6fd6');R(p[0]-4,p[1]+1,9,1,'#3b6fd6');c.fillStyle='#ffd84a';c.fillText('FILE',p[0]-8,p[1]-17)}
  [mgr,ceo,emp].forEach(step);
  person(emp,'#2f9e5b','#3b2a1a',null,{work:scene_.maWorking&&emp.sit,sad:scene_.maAlert});
  person(mgr,'#2b5fbf','#171717','#e0443e',{work:!mgrAsleep&&(mgr.sit),sleep:mgrAsleep});
  person(ceo,'#1c1c24','#d8d8d8','#ffd84a',{hat:'#ffd84a'});
  if(!emp.vis&&!sim){c.fillStyle='#8b93a1';c.fillText('(empty desk)',216,100)}
  if(sim){R(W-84,2,82,12,'#8a4fd1');c.fillStyle='#fff';c.fillText('SIMULATED',W-78,4)}
  tick++;requestAnimationFrame(draw);
}
// ---------------- the text box (one line at a time, each can be awaited) ----------------
const LAST={say:Date.now()};
let sayChain=Promise.resolve(),pending=0,fast=false;
const SPEAKER={MANAGER:'#2b5fbf',EMPLOYEE:'#2f9e5b',CEO:'#b8860b',CABLE:'#555'};
function say(who,text){
  if(SIM.on)text='[SIMULATED] '+text;
  pending++;
  return sayChain=sayChain.then(()=>new Promise(res=>{
    $('who').textContent=who;$('who').style.background=SPEAKER[who]||'#181425';$('arrow').style.visibility='hidden';
    const el=$('txt');el.textContent='';let i=0;
    const timer=setInterval(()=>{i+=fast?3:1;el.textContent=text.slice(0,i);
      if(i>=text.length){clearInterval(timer);$('arrow').style.visibility='visible';pending--;LAST.say=Date.now();setTimeout(res,pending>3?700:Math.min(2600,900+text.length*22))}},26);
  }));
}
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT')return;
  if(e.key===' '||e.key==='Enter'){fast=true;setTimeout(()=>fast=false,400);e.preventDefault()}
  if(e.key==='d'||e.key==='D')playDemo();
});
// ---------------- scenes (one after the other) ----------------
let chain=Promise.resolve();
const scene=fn=>chain=chain.then(fn).catch(e=>console.error(e));
const TO_MGR_E=[[252,128],[96,128],TALK_E];
async function ceoBrief(text){
  ceo.vis=true;ceo.x=-20;ceo.y=130;
  await go(ceo,[[60,130],TALK]);ceo.face=-1;ceo.bubble='!';ceo.bcol='#ffd84a';
  await say('CEO',text.length>150?text.slice(0,147)+'...':text);ceo.bubble='';
  mgr.face=1;await say('MANAGER','Understood, sir. I will read it, ask what is missing, and write the leadgen file.');
  await go(ceo,[[60,130],[-30,130]]);ceo.vis=false;
}
async function managerStartsWriting(){scene_.thinking=true;mgr.bubble='...';mgr.bcol='#f8f4e8';await say('MANAGER','Reading the request... (my model runs on this machine, it can take a few minutes)')}
async function managerAsks(){scene_.thinking=false;mgr.bubble='?';mgr.bcol='#ffd84a';await say('MANAGER','CEO, I have questions. I cannot write the file without your answers.')}
async function fileReady(){scene_.thinking=false;scene_.fileOnDesk=true;mgr.bubble='';await say('MANAGER','The leadgen file is ready, sir. Please read it and approve it below. Nothing is sent until you do.')}
async function deliver(){
  scene_.fileOnDesk=false;mgr.bubble='';await say('MANAGER','Approved. The file is live. Calling the Marketing Agent now...');
  mgr.bubble='tel';mgr.bcol='#cfe8ff';scene_.token={d:0};
  while(scene_.token.d<CLEN){scene_.token.d+=2.2;await sleep(16)}
  scene_.token=null;mgr.bubble='';
  await say('CABLE','The leadgen file reached the Marketing Floor.');
}
async function empEnters(){
  if(emp.vis&&emp.sit)return;
  emp.vis=true;emp.x=352;emp.y=130;emp.face=-1;emp.bubble='';
  await go(emp,[[300,130],[252,130],SEAT_E]);emp.sit=true;emp.face=-1;
}
async function empSeesFile(){
  await empEnters();scene_.maWorking=true;emp.bubble='...';
  await say('EMPLOYEE','Got the leadgen file. Sitting down at my table and starting the work.');
}
async function empBlocker(msg){
  scene_.maAlert=true;scene_.maWorking=false;emp.bubble='!';emp.bcol='#ffd0cc';
  await say('EMPLOYEE','Boss, I am stuck. '+msg);
  await go(emp,TO_MGR_E);emp.face=-1;mgr.face=1;
  await say('MANAGER','I understand. I will tell the CEO, and keep this open until you say it is fixed.');
  emp.bubble='';
}
async function empFixed(msg){
  scene_.maAlert=false;emp.bubble='ok';emp.bcol='#d6ffd6';await say('EMPLOYEE',msg||'It is fixed. Back to work.');
  await go(emp,[[96,128],[252,128],SEAT_E]);emp.sit=true;emp.bubble='...';emp.bcol='#f8f4e8';scene_.maWorking=true;
}
async function empDone(msg){
  scene_.maWorking=false;emp.bubble='ok';emp.bcol='#d6ffd6';
  await go(emp,TO_MGR_E);emp.face=-1;mgr.face=1;
  await say('EMPLOYEE',msg||'Task complete, boss. The pass is finished.');
  await say('MANAGER','Well done. I will tell the CEO.');emp.bubble='';
  await go(emp,[[96,128],[252,128],SEAT_E]);emp.sit=true;
}
// ---------------- talking to the Campaign Agent ----------------
async function api(path,method,body){
  const r=await fetch(path,{method:method||'GET',cache:'no-store',headers:{'Content-Type':'application/json','X-CA-Window':'1'},body:body?JSON.stringify(body):undefined});
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||j.error||('HTTP '+r.status));return j;
}
const note=t=>$('note').textContent=t||'';
const PRESET='Find automotive and industrial automation companies in Germany that need embedded Linux and Yocto engineering help.';
$('preset').onclick=()=>{$('msg').value=PRESET;note('Yesterday\'s test campaign is filled in. Press Send.')};
$('send').onclick=async()=>{
  const text=$('msg').value.trim();if(!text||SIM.on)return;
  $('send').disabled=true;note('Sending...');
  try{
    const j=await api('/api/campaigns','POST',{message:text});
    Object.assign(F,{id:j.campaign_id,state:'NEW',busy:true,live:false,q:[],cand:null,failed:null,shown:'',yaml:false,qsig:'',asig:''});
    $('qs').innerHTML='';$('act').innerHTML='';$('yaml').hidden=true;$('msg').value='';note('The manager has the message.');
    scene(()=>ceoBrief(text));pollFlow();
  }catch(e){note('Could not send: '+e.message);$('send').disabled=false}
};
function el(tag,txt,cls){const e=document.createElement(tag);if(txt!==undefined)e.textContent=txt;if(cls)e.className=cls;return e}
function renderQuestions(){
  const box=$('qs'),show=F.q.length&&F.state==='CLARIFICATION_REQUIRED'&&!F.busy;
  const sig=show?F.q.map(q=>q.id).join():'';
  if(sig===F.qsig)return;F.qsig=sig;      // only when the questions change: a half-made choice must survive the next poll
  box.innerHTML='';if(!show)return;
  const picked={};
  F.q.forEach(q=>{
    const d=el('div','','q');d.appendChild(el('p',q.text));picked[q.id]=new Set();
    q.choices.forEach(ch=>{const b=el('button',ch.label,'ch');b.onclick=()=>{
      if(q.multi){picked[q.id].has(ch.id)?picked[q.id].delete(ch.id):picked[q.id].add(ch.id)}else{picked[q.id]=new Set([ch.id])}
      d.querySelectorAll('.ch').forEach((x,i)=>x.classList.toggle('on',picked[q.id].has(q.choices[i].id)))};d.appendChild(b)});
    if(q.allow_text){const i=el('input');i.placeholder='or type your own answer';i.id='t_'+q.id;d.appendChild(el('div'));d.appendChild(i)}
    box.appendChild(d);
  });
  const b=el('button','Send my answers','go');b.onclick=async()=>{
    const answers={};F.q.forEach(q=>{const t=($('t_'+q.id)||{}).value;const ids=[...picked[q.id]];answers[q.id]=t&&t.trim()?t.trim():(ids.length?ids.join(','):'skip')});
    b.disabled=true;try{await api('/api/campaigns/'+F.id+'/answer','POST',{answers});F.busy=true;F.shown='';F.qsig='';box.innerHTML='';scene(()=>say('CEO','Here are my answers.').then(managerStartsWriting))}catch(e){note(e.message);b.disabled=false}};
  box.appendChild(b);
}
function renderActions(){
  const a=$('act');
  const sig=JSON.stringify([F.state,F.busy,F.live,F.cand&&F.cand.version,F.notes.length,F.pipe&&F.pipe.desired_state,F.pipe&&F.pipe.actual_state]);
  if(sig===F.asig)return;F.asig=sig;      // only when something changed: text typed into the edit box must survive the next poll
  a.innerHTML='';
  if(F.state==='AWAITING_REVIEW'&&F.cand&&!F.busy){
    a.appendChild(el('span','Version '+(F.cand.version||'?')+' checks: '+F.cand.levels.map(l=>'L'+l.level+' '+l.status).join(', ')));
    F.notes.forEach(n=>a.appendChild(el('div','Note: '+n.reason,'')));
    const y=el('button','Read the file');y.onclick=()=>{const p=$('yaml');p.hidden=!p.hidden;p.textContent=F.cand.yaml};a.appendChild(y);
    const ei=el('input');ei.id='editText';ei.placeholder='or ask for a change in words (e.g. also Austria)';ei.style.width='min(100%,340px)';a.appendChild(ei);
    const eb=el('button','Ask for this change');eb.onclick=async()=>{const t=ei.value.trim();if(!t)return;eb.disabled=true;try{await sendEdit(t)}catch(e){note(e.message);eb.disabled=false}};a.appendChild(eb);
    const ok=el('button','Approve, publish and start the Marketing Agent','go');ok.onclick=async()=>{ok.disabled=true;try{await api('/api/campaigns/'+F.id+'/approve','POST',{start_pipeline:true});F.busy=true;F.shown='';F.told=false;scene(()=>say('CEO','Approved. Publish it and tell the Marketing Agent to start.'))}catch(e){note(e.message);ok.disabled=false}};a.appendChild(ok);
    const ok2=el('button','Approve and publish only');ok2.onclick=async()=>{ok2.disabled=true;try{await api('/api/campaigns/'+F.id+'/approve','POST',{start_pipeline:false});F.busy=true;F.shown='';F.told=true;scene(()=>say('CEO','Approved. Publish it. I will start the Marketing Agent myself.'))}catch(e){note(e.message);ok2.disabled=false}};a.appendChild(ok2);
  }
  if(F.live){
    const up=F.pipe&&F.pipe.actual_state==='running',asked=F.pipe&&F.pipe.desired_state==='running'&&!up;
    const s=el('button',up?'Marketing Agent is running':asked?'Asked. Waiting for the Marketing Agent to answer...':'Tell the Marketing Agent to start','go');s.disabled=up||asked;
    s.onclick=async()=>{s.disabled=true;try{const j=await api('/api/pipeline','POST',{state:'running'});
      if(j.ok){scene(()=>say('MANAGER','Marketing Agent, please start the work with the file I sent. I will tell you when it answers.'));verifyStart()}
      else scene(()=>say('MANAGER','I am not allowed to start the Marketing Agent yet. An administrator must apply migration 0018. You can press Start on the Marketing Agent dashboard.'))}catch(e){note(e.message)}};
    a.appendChild(s);
    if(up||asked){const x=el('button','Stop it','no');x.onclick=async()=>{await api('/api/pipeline','POST',{state:'stopped'}).catch(e=>note(e.message))};a.appendChild(x)}
    const n=el('button','New campaign');n.onclick=()=>{F.id=null;F.live=false;$('act').innerHTML='';$('send').disabled=false;note('')};a.appendChild(n);
  }
}
// asking is not the same as it starting: wait for the Marketing Agent itself to say it is running
async function verifyStart(){
  VERIFYING.on=true;const t=Date.now();
  while(Date.now()-t<45000){
    await sleep(3000);
    try{const p=(await api('/api/pipeline')).pipeline;F.pipe=p;if(p&&p.actual_state==='running'){PIPE.seen='running';VERIFYING.on=false;scene(()=>say('EMPLOYEE','I am on it, boss. Opening Chrome now.'));return}}catch(e){}
  }
  VERIFYING.on=false;
  scene(()=>say('MANAGER','I asked the Marketing Agent to start, but it has not answered for 45 seconds. Its service is probably stopped on the Marketing Agent machine. Start it there: bash deploy/remote.sh up, in the Marketing Agent folder.'));
}
// a window that was closed or restarted picks up where it was: what is true lives in the database, not in this page
const RESUME={promise:null,resumed:false};
async function resume(){
  if(F.id||SIM.on)return;
  try{
    const s=(await api('/api/campaigns/latest')).campaign;if(!s||F.id)return;
    RESUME.resumed=true;
    Object.assign(F,{id:s.campaign_id,state:s.state,busy:s.busy,q:s.questions,cand:s.candidate,notes:s.notes||[],live:s.live,failed:s.failed,shown:'',qsig:'',asig:''});
    note('Resumed: '+s.name);
    const what=s.live?'The leadgen file for "'+s.name+'" is live. My part is done.':'We were working on "'+s.name+'" ('+s.state.toLowerCase().replace(/_/g,' ')+').';
    scene(()=>say('MANAGER','Welcome back, sir. '+what));
    pollFlow();
  }catch(e){}
}
function ago(sec){return sec<90?Math.round(sec)+' s':sec<5400?Math.round(sec/60)+' min':Math.round(sec/3600)+' h'}
function liveStatus(){
  const line=n=>data&&data.lines.find(l=>l.name===n);
  const ma=line('Marketing Agent'),wk=line('Worker'),p=F.pipe;
  const parts=[];
  if(p)parts.push('pipeline '+p.actual_state+(p.current_step?' ('+p.current_step+' step)':'')+(p.heartbeat_age!=null?', heartbeat '+ago(p.heartbeat_age)+' ago':''));
  if(ma)parts.push('Marketing Agent: '+ma.detail);
  if(wk)parts.push('manager: '+wk.detail.split(',')[0]);
  return {who:S.maWorking?'EMPLOYEE':'MANAGER',text:'Status at '+new Date().toLocaleTimeString()+'. '+parts.join('. ')+'.'};
}
setInterval(()=>{   // quiet for 45 s, nothing queued, no campaign being written or asked about: say how things stand
  if(SIM.on||pending>0||!data||!seen.lines||Date.now()-LAST.say<45000)return;
  if(F.busy||(F.state==='CLARIFICATION_REQUIRED'&&F.q.length)||F.state==='AWAITING_REVIEW')return;
  const s=liveStatus();say(s.who,s.text);
},10000);
async function pollFlow(){
  if(!F.id||SIM.on){return}
  try{
    const s=await api('/api/campaigns/'+F.id);
    if(s.gone){ // the campaign was deleted (a clean-up): forget it, as a fresh window would
      Object.assign(F,{id:null,state:'NEW',busy:false,live:false,q:[],cand:null,notes:[],failed:null,shown:'',qsig:'',asig:''});
      $('qs').innerHTML='';$('act').innerHTML='';$('yaml').hidden=true;$('send').disabled=false;note('The earlier campaign is gone. Ready for a new one.');
      scene(()=>say('MANAGER','The earlier campaign was cleared away, sir. We start from the beginning. What campaign do you want?'));return;
    }
    const was=F.state;Object.assign(F,{state:s.state,busy:s.busy,q:s.questions,cand:s.candidate,notes:s.notes||[],live:s.live,failed:s.failed});
    const key=F.state+'|'+F.busy+'|'+F.live+'|'+(F.failed||'');
    if(key!==F.shown){const first=F.shown==='';F.shown=key;
      if(F.failed)scene(()=>say('MANAGER','Something went wrong with that request: '+F.failed+'. You can try again.'));
      else if(F.busy&&(F.state==='NEW'||F.state==='DRAFT'||F.state==='INTERPRETING'||F.state==='AWAITING_REVIEW'||F.state==='CLARIFICATION_REQUIRED')&&!scene_.thinking)scene(managerStartsWriting);
      else if(!F.busy&&F.state==='CLARIFICATION_REQUIRED')scene(managerAsks);
      else if(!F.busy&&F.state==='AWAITING_REVIEW')scene(fileReady);
      else if(F.live&&!first)scene(deliver);
      else if(!F.busy&&F.state==='MODEL_UNAVAILABLE')scene(()=>say('MANAGER','My model did not answer. Please send the message again.'));
    }
    if(F.live&&F.pnote&&!F.told){F.told=true;const n=F.pnote;
      scene(()=>say('MANAGER',n.asked?(n.restarted?'The Marketing Agent was already working, so I asked it to stop and start again on the new file.':'I asked the Marketing Agent to start. It will pick up the new file at the start of its pass.'):'I could not start the Marketing Agent ('+(n.reason||'unknown')+'). Press Start on its dashboard.'))}
    renderQuestions();renderActions();
    $('send').disabled=!!F.id&&!F.live&&F.state!=='MODEL_UNAVAILABLE'&&F.state!=='DRAFT'&&!F.failed;
  }catch(e){note(e.message)}
  setTimeout(pollFlow,3500);
}
// ---------------- what the Marketing Agent really reports ----------------
const strip=s=>s.replace(/^\s*\S+\s+/,'');  // an event line starts with its time (UTC), so every event is unique
const seen={lines:null,ev:new Set()};
const WORDS=s=>s.split(/\s+/);
function eventScene(k){
  const t=WORDS(k),kind=t[0],code=t[2]||'',msg=k.split(code)[1]?.trim()||'';
  if(code==='MA_CAMPAIGN_APPLIED')scene(empSeesFile);
  else if(code==='MA_PASS_STARTED')scene(async()=>{if(!(emp.vis&&emp.sit)){await empEnters()}scene_.maWorking=true;emp.bubble='...';await say('EMPLOYEE','A new pass has started. I am working on the leadgen file.')});
  else if(kind==='blocker'){if(!scene_.maAlert){scene_.maAlert=true;scene(()=>empBlocker(msg))}}  // a repeated blocker is the same blocker
  else if(kind==='resolved'&&code!=='MA_CAMPAIGN_REJECTED'&&scene_.maAlert)scene(()=>empFixed('Fixed: '+msg));
  else if(code==='MA_PASS_FINISHED'&&/finished$/.test(msg))scene(()=>empDone());
  else if(code==='MA_PASS_FINISHED')scene(()=>say('EMPLOYEE',msg+'.'));
}
// Requests that come from outside this window (the dashboard, the CA's own automatic start): the manager says so, so the office
// shows the work as it happens and not only what is typed in here. The ones sent from this window are narrated by its own flow.
const JOBS={seen:null,ours:new Set(['ceo-window','dev-client'])};
const JOB_WHAT={interpret:'a new campaign',answer:'answers to my questions',edit:'a change',approve:'an approval',discard:'a rejection',rollback:'a rollback',deactivate:'a switch-off',suggestion:'a suggestion'};
function watchJobs(d){
  const jobs=d.jobs||[];
  if(JOBS.seen===null){JOBS.seen=new Map(jobs.map(j=>[j.id,j.status]));return}   // the first look: what is already there is not news
  [...jobs].reverse().forEach(j=>{                                                // oldest first
    const was=JOBS.seen.get(j.id);JOBS.seen.set(j.id,j.status);
    if(SIM.on||VERIFYING.on||JOBS.ours.has(j.by)||was===j.status)return;
    const what=JOB_WHAT[j.type]||j.type,name=j.name?' "'+j.name+'"':'';
    const who=String(j.by||'').startsWith('campaign-agent')?'the dashboard (I found it myself)':String(j.by||'someone').split('@')[0];
    if(was===undefined&&(j.status==='pending'||j.status==='processing'||j.age_s<180)){
      scene(async()=>{mgr.bubble='mail!';mgr.bcol='#ffd84a';await say('MANAGER','A letter from the dashboard: '+what+name+', sent by '+who+'. I am on it.')});
    }
    if(j.status==='processing'&&was!=='processing')scene(()=>say('MANAGER','Reading '+what+name+' now. My model runs on this machine, so it takes a few minutes.'));
    if(j.status==='done'&&was!=='done'){
      const next={AWAITING_REVIEW:'The leadgen file is ready. Please review it and approve or change it in the dashboard.',
        CLARIFICATION_REQUIRED:'I need answers to my questions. Please answer them in the dashboard.',
        PUBLISHED:'It is live now.',VALIDATION_FAILED:'The checks failed. Please look at them in the dashboard.'}[j.state];
      scene(async()=>{mgr.bubble='';scene_.fileOnDesk=j.state==='AWAITING_REVIEW';await say('MANAGER','Finished '+what+name+'. '+(next||''))});
    }
    if(j.status==='failed'&&was!=='failed')scene(()=>say('MANAGER','Something went wrong with '+what+name+': '+(j.error||'unknown')+'. It can be sent again.'));
  });
}
function narrate(d){
  if(!seen.lines){
    if(!RESUME.resumed)say('MANAGER','Good morning, sir! Let me look around the office...');
    d.events.forEach(r=>seen.ev.add(strip(r)));seen.lines={};d.lines.forEach(l=>seen.lines[l.name]=l.state);
    const bad=d.lines.filter(l=>l.state==='fail'||l.state==='warn');
    if(bad.length)bad.forEach(l=>say(l.name==='Marketing Agent'?'EMPLOYEE':'MANAGER',(l.state==='fail'?'Trouble! ':'Careful. ')+l.name+': '+l.detail));
    else if(!RESUME.resumed)say('MANAGER','Everything is in order. Tell me what campaign you want.');   // when we resumed, the welcome-back line already says where we are
    if(S.ma!=='off'){emp.vis=true;emp.x=SEAT_E[0];emp.y=SEAT_E[1];emp.sit=true;emp.face=-1;scene_.maWorking=S.maWorking;emp.bubble=S.maWorking?'...':'';if(S.blockers){scene_.maAlert=true;scene_.maWorking=false;emp.bubble='!';emp.bcol='#ffd0cc'}}
    return;
  }
  d.lines.forEach(l=>{const was=seen.lines[l.name];if(was!==undefined&&was!==l.state&&pending<4){
    const who=l.name==='Marketing Agent'?'EMPLOYEE':'MANAGER';
    say(who,(l.state==='ok'?'Back to normal. ':l.state==='fail'?'Trouble! ':l.state==='warn'?'Careful. ':'Switched off. ')+l.name+': '+l.detail)}
    seen.lines[l.name]=l.state});
  [...d.events].reverse().forEach(r=>{const k=strip(r);if(!seen.ev.has(k)){seen.ev.add(k);if(!SIM.on)eventScene(k)}});
}
// what the Marketing Agent does with its pipeline is said out loud when it changes: stopped, or running again after a restart
const PIPE={seen:null};
function announcePipeline(p){
  const now=p&&p.actual_state;if(!now)return;
  const was=PIPE.seen;PIPE.seen=now;
  if(was===null){   // the first look: if the Marketing Agent is already working, say so, as when it started
    if(now==='running'&&!SIM.on){scene_.maWorking=true;
      scene(async()=>{if(!(emp.vis&&emp.sit))await empEnters();emp.bubble='...';
        await say('EMPLOYEE','Marketing Agent started. I am running a pass'+(p.current_step?' ('+p.current_step+' step)':'')+' on the leadgen file the manager sent me.')})}
    return;
  }
  if(was===now||SIM.on||VERIFYING.on)return;      // no change, the demo, or "Start" is already being followed
  if(now==='running'){scene_.maWorking=true;scene(async()=>{if(!(emp.vis&&emp.sit))await empEnters();emp.bubble='...';await say('EMPLOYEE','I am running again, boss. I have started a new pass.')})}
  else{scene_.maWorking=false;scene(()=>say('EMPLOYEE','I have stopped working, boss. Waiting for the start signal.'))}
}
const VERIFYING={on:false};
async function poll(){
  if(RESUME.promise&&!seen.lines){await RESUME.promise.catch(()=>{})}   // the first greeting must know whether we resumed
  try{data=await api('/api/state');
    if(!SIM.on){readState();narrate(data);watchJobs(data);const p=await api('/api/pipeline').catch(()=>null);if(p){announcePipeline(p.pipeline);F.pipe=p.pipeline}}
    $('reqs').textContent=data.requests.length?data.requests.join('\n'):'none';$('evs').textContent=data.events.length?data.events.join('\n'):'none';
    $('panel').innerHTML='';data.lines.forEach(l=>{const r=el('div','','row');r.appendChild(el('span','','dot '+l.state));r.appendChild(el('span',l.name,'nm'));r.appendChild(el('span',l.detail,'dt'));$('panel').appendChild(r)});
  }catch(e){if(!SIM.on&&!seen.lost){seen.lost=true;say('MANAGER','I lost the phone line to this window. The Campaign Agent on this machine is not answering.')}}
  setTimeout(poll,2500);
}
// ---------------- the demo: the whole story, scripted, always tagged SIMULATED ----------------
async function playDemo(){
  if(SIM.on)return;SIM.on=true;$('mode').textContent='SIMULATED';$('mode').className='pill sim';$('bd').disabled=true;
  const keep={ma:emp.vis,sit:emp.sit};emp.vis=false;emp.sit=false;emp.x=352;scene_.maWorking=false;
  await chain;
  await scene(()=>ceoBrief('Find automotive and industrial automation companies in Germany that need embedded Linux and Yocto help.'));
  await scene(managerStartsWriting);await sleep(2500);
  await scene(managerAsks);await scene(()=>say('CEO','Automotive and industrial automation. Embedded Linux and Yocto.'));
  await scene(managerStartsWriting);await sleep(2000);
  await scene(fileReady);await scene(()=>say('CEO','Approved.'));
  await scene(deliver);await scene(empSeesFile);await sleep(2500);
  await scene(()=>empBlocker('Chrome did not start on my desktop.'));await sleep(1500);
  await scene(()=>empFixed('Chrome is back. Continuing.'));await sleep(2500);
  await scene(()=>empDone('Task complete, boss. 6 new leads found.'));
  await scene(()=>say('MANAGER','End of the demo. Back to the real office.'));
  SIM.on=false;scene_.thinking=false;scene_.fileOnDesk=false;mgr.bubble='';emp.vis=keep.ma;emp.sit=keep.sit;scene_.maWorking=keep.ma&&S.maWorking;
  if(!keep.ma){emp.x=352}else{emp.x=SEAT_E[0];emp.y=SEAT_E[1]}
  $('mode').textContent='LIVE';$('mode').className='pill live';$('bd').disabled=false;
}
let caCtl=null;
async function refreshControl(){try{const j=await api('/api/ca-control');caCtl=j.control;
  const p=caCtl&&caCtl.desired_state==='paused';$('bp').textContent=p?'Resume CA':'Pause CA';$('bp').disabled=!caCtl;
  $('bp').title=caCtl?('Asked: '+caCtl.desired_state+'. Doing: '+caCtl.actual_state):'Apply migration 0019 first'}catch(e){}}
$('bp').onclick=async()=>{const want=caCtl&&caCtl.desired_state==='paused'?'running':'paused';$('bp').disabled=true;
  try{const j=await api('/api/ca-control','POST',{state:want});
    if(!j.ok)say('MANAGER','I cannot be paused from here yet: migration 0019 is not applied.');
    else say('MANAGER',want==='paused'?'Understood. I finish what I am doing, then I take no new request, and I tell the Marketing Agent to stop too, until you resume me.':'Back to work. I will take the waiting requests now, and start the Marketing Agent again if it was running.')}catch(e){note(e.message)}
  await refreshControl()};
setInterval(refreshControl,5000);refreshControl();
$('bd').onclick=playDemo;

// ---------------- the edit request, as the CEO would ask for it ----------------
async function sendEdit(text){
  await api('/api/campaigns/'+F.id+'/edit','POST',{text});
  F.busy=true;F.shown='';$('act').innerHTML='';$('yaml').hidden=true;
  scene(async()=>{await say('CEO','One change: '+text);await say('MANAGER','Understood, sir. I will change the file and show it to you again.')});
}
// ---------------- autopilot: a plan left by `python -m campaign_agent autopilot` is carried out here, step by step,
// with the same buttons a person would press; each step is reported back to the command ----------------
const AP={id:null};
const apSay=(line,state)=>api('/api/autopilot/progress','POST',{id:AP.id,line,state:state||'running'}).catch(()=>{});
async function waitUntil(test,ms,what){const t=Date.now();while(Date.now()-t<ms){const v=test();if(v)return v;await sleep(1000)}throw new Error('timed out waiting for '+what)}
function autoAnswer(mode){
  // each question: click the choices the plan names; if it names none of them, type the next unused answer into the free-text box
  // (the first choice only when the plan has nothing left to give)
  const given=Array.isArray(mode)?mode.slice():[];const used=new Set();
  document.querySelectorAll('#qs .q').forEach(q=>{
    const btns=[...q.querySelectorAll('.ch')],box=q.querySelector('input');
    const hit=btns.filter(b=>given.some((m,i)=>b.textContent.toLowerCase().includes(m.toLowerCase())&&(used.add(i),true)));
    if(hit.length){hit.forEach(b=>b.click());return}
    const next=given.findIndex((m,i)=>!used.has(i));
    if(box&&next>=0){used.add(next);box.value=given[next];return}
    if(btns.length)btns[0].click();
  });
  const send=[...document.querySelectorAll('#qs button')].find(b=>/Send my answers/.test(b.textContent));
  if(!send)throw new Error('the answer button is not on the screen');send.click();
}
async function startMa(p){
  if(!p.start){await apSay('done: the campaign is live; the Marketing Agent was not asked to start','done');return}
  await sleep(4000);
  const btn=await waitUntil(()=>[...$('act').querySelectorAll('button')].find(b=>/Tell the Marketing Agent|Marketing Agent is running/.test(b.textContent)),30000,'the start button');
  if(/is running/.test(btn.textContent)){await apSay('the Marketing Agent was already running','done');return}
  btn.click();await apSay('pressed "Tell the Marketing Agent to start"');
  await waitUntil(()=>F.pipe&&F.pipe.actual_state==='running',90000,'the Marketing Agent to answer that it is running');
  await apSay('the Marketing Agent answered: it is running','done');
}
async function runPlan(p){
  try{
    SIM.on=false;await sleep(500);
    const before=F.id;
    if($('send').disabled)throw new Error('the window is busy with another campaign: approve or finish it first');
    $('msg').value=p.message;$('send').click();await apSay('typed the campaign and pressed Send');
    await waitUntil(()=>F.id&&F.id!==before,30000,'the manager to take the message');
    const end=Date.now()+p.timeout*1000;let edited=false,answered='';
    while(Date.now()<end){
      await sleep(3000);
      if(F.failed)throw new Error('the request failed: '+F.failed);
      if(F.busy)continue;
      if(F.state==='CLARIFICATION_REQUIRED'&&F.q.length){
        const key=F.q.map(q=>q.id).join();
        if(key!==answered){answered=key;await sleep(1500);autoAnswer(p.answers);await apSay('answered '+F.q.length+' question(s)')}
        continue;
      }
      if(F.state==='AWAITING_REVIEW'&&F.cand){
        if(p.edit&&!edited){edited=true;await sendEdit(p.edit);await apSay('sent the edit request: '+p.edit);continue}
        if(!p.approve){await apSay('the leadgen file is ready; not approving (asked not to)','done');return}
        const ok=[...$('act').querySelectorAll('button')].find(b=>/Approve/.test(b.textContent));
        if(!ok)continue;
        await sleep(1500);ok.click();F.busy=true;await apSay('pressed Approve');
        await waitUntil(()=>F.live,600000,'the campaign to go live');
        await apSay('the campaign is live (version '+(F.cand&&F.cand.version||'?')+')');
        await startMa(p);return;
      }
      if(F.live){await apSay('the campaign is live');await startMa(p);return}
    }
    throw new Error('timed out');
  }catch(e){await apSay(String(e.message||e),'failed')}
}
async function apPoll(){
  try{const j=await api('/api/autopilot');if(j.plan&&j.plan.id!==AP.id){AP.id=j.plan.id;apSay('the window took the plan');runPlan(j.plan)}}catch(e){}
  setTimeout(apPoll,2000);
}
apPoll();
RESUME.promise=resume();poll();draw();
</script></body></html>"""
