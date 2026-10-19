const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../skills_bundled/visualize');
function element(extra={}) {
  return Object.assign({listeners:{},dataset:{},addEventListener(type,fn){this.listeners[type]=fn;}},extra);
}
function runtime(section) {
  const buttons={theme:element(),download:element()};
  const context={console,Map,Math,JSON,Array,Number,String,Boolean,Set,Blob,URL,setTimeout,
    document:{documentElement:{outerHTML:'<html>original</html>',dataset:{theme:'dark'}},
      getElementById(id){return id==='page-data'?{textContent:JSON.stringify({language:'en',sections:[section]})}:buttons[id];},
      querySelectorAll(){return [];},addEventListener(){},dispatchEvent(){}},
    Event:function(){},window:{devicePixelRatio:1},requestAnimationFrame(fn){fn();},
    getComputedStyle(){return {getPropertyValue(name){return name==='--text'?'#edf2f8':'#88b4ff';}};},
    ResizeObserver:class{observe(){}},};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root,'page.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'scene.js'),'utf8'),context);
  return context;
}
const section={type:'scene3d',objects:[
  {id:'a',label:'Gateway',shape:'box',position:[-3,0,0]},
  {id:'b',label:'Worker',shape:'sphere',position:[0,2,1]},
  {id:'c',label:'Store',shape:'cone',position:[3,0,-1]}],edges:[{from:'a',to:'b'}]};
test('CSV preserves numbers, quotes and Cyrillic while neutralizing formulas',()=>{
  const context=runtime(section);
  assert.equal(context.csvCell(-3),'"-3"');
  assert.equal(context.csvCell('=HYPERLINK("bad")'),'"\'=HYPERLINK(""bad"")"');
  assert.equal(context.csvCell('  +SUM(A1)'),'"\'  +SUM(A1)"');
  assert.equal(context.csvCell('Привет, "мир"'),'"Привет, ""мир"""');
  assert.equal(context.csvCell(null),'""');
});
test('3D geometry is finite and rotation preserves distance',()=>{
  const context=runtime(section);
  for(const shape of ['box','sphere','cone']) {
    const mesh=context.sceneMesh(shape);
    assert.ok(mesh.vertices.length>=8 && mesh.faces.length>=6);
    mesh.faces.forEach(face=>face.forEach(index=>assert.ok(mesh.vertices[index])));
    mesh.vertices.forEach(p=>assert.ok(p.every(Number.isFinite)));
  }
  const point=context.rotatePoint([1,2,3],.6,-.8);
  assert.ok(Math.abs(Math.hypot(...point)-Math.sqrt(14))<1e-10);
});
test('3D draws actual faces and supports keyboard, button, pointer, resize and reset',()=>{
  const context=runtime(section),operations=[];
  const ctx={};
  for(const name of ['setTransform','clearRect','beginPath','moveTo','lineTo','stroke','closePath','fill','fillRect','fillText']) {
    ctx[name]=(...args)=>{args.filter(v=>typeof v==='number').forEach(v=>assert.ok(Number.isFinite(v)));operations.push([name,...args]);};
  }
  ctx.measureText=text=>({width:text.length*7});
  const canvas=element({clientWidth:1200,clientHeight:700,width:0,height:0,getContext(){return ctx;},focus(){context.document.activeElement=this;},setPointerCapture(){}});
  const status={textContent:'Drag to orbit'}, reset=element();
  const turns=['left','right','up','down'].map(turn=>element({dataset:{turn}}));
  const zooms=['in','out'].map(zoom=>element({dataset:{zoom}}));
  const panel={closest(){return {dataset:{index:'0'}};},querySelector(q){return q==='canvas'?canvas:q==='[data-reset]'?reset:status;},querySelectorAll(q){return q==='[data-turn]'?turns:zooms;}};
  context.initScene(panel);
  assert.ok(operations.filter(o=>o[0]==='fill').length>100);
  assert.ok(operations.some(o=>o[0]==='fillText' && o[1]==='Worker'));
  const initial=canvas.dataset.view;
  canvas.listeners.keydown({key:'ArrowRight',preventDefault(){}});assert.notEqual(canvas.dataset.view,initial);
  reset.listeners.click();assert.equal(canvas.dataset.view,initial);
  zooms[0].listeners.click();assert.notEqual(canvas.dataset.view,initial);
  reset.listeners.click();
  canvas.listeners.pointerdown({button:0,clientX:20,clientY:20,pointerId:1});
  canvas.listeners.pointermove({clientX:60,clientY:50});assert.notEqual(canvas.dataset.view,initial);
  canvas.listeners.pointercancel();const after=canvas.dataset.view;
  canvas.listeners.pointermove({clientX:100,clientY:80});assert.equal(canvas.dataset.view,after);
  canvas.listeners.keydown({key:'Home',preventDefault(){}});assert.equal(canvas.dataset.view,initial);
  canvas.clientWidth=390;zooms[0].listeners.click();assert.equal(canvas.width,390);
});
test('missing canvas support presents a readable fallback',()=>{
  const context=runtime(section),status={textContent:''};
  context.initScene({closest(){return {dataset:{index:'0'}};},querySelector(q){return q==='canvas'?{getContext(){return null;}}:status;}});
  assert.match(status.textContent,/unavailable/);
});
