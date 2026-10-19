function sceneMesh(shape) {
  if (shape === 'box') return {
    vertices: [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]],
    faces: [[0,1,2,3],[4,7,6,5],[0,4,5,1],[3,2,6,7],[0,3,7,4],[1,5,6,2]]
  };
  if (shape === 'cone') {
    const vertices = [[0,1,0], ...Array.from({length:16}, (_,i) => [Math.cos(i*Math.PI/8),-1,Math.sin(i*Math.PI/8)])];
    return {vertices, faces: [...Array.from({length:16}, (_,i) => [0,i+1,(i+1)%16+1]), Array.from({length:16}, (_,i) => i+1)]};
  }
  const vertices = [], faces = [];
  for (let y=0; y<=8; y++) for (let x=0; x<12; x++) {
    const phi=y*Math.PI/8, theta=x*Math.PI/6;
    vertices.push([Math.sin(phi)*Math.cos(theta), Math.cos(phi), Math.sin(phi)*Math.sin(theta)]);
  }
  for (let y=0; y<8; y++) for (let x=0; x<12; x++) faces.push([y*12+x,y*12+(x+1)%12,(y+1)*12+(x+1)%12,(y+1)*12+x]);
  return {vertices,faces};
}
function rotatePoint(point, yaw, pitch) {
  const [x,y,z] = point, cy=Math.cos(yaw), sy=Math.sin(yaw), cp=Math.cos(pitch), sp=Math.sin(pitch);
  return [x*cy+z*sy, y*cp-(-x*sy+z*cy)*sp, y*sp+(-x*sy+z*cy)*cp];
}
function sceneBounds(objects) {
  const low=[Infinity,Infinity,Infinity], high=[-Infinity,-Infinity,-Infinity];
  objects.forEach(o => o.position.forEach((v,i) => { low[i]=Math.min(low[i],v-(o.size||1)); high[i]=Math.max(high[i],v+(o.size||1)); }));
  return {center:low.map((v,i)=>(v+high[i])/2), radius:Math.max(...high.map((v,i)=>v-low[i]))/2 || 1};
}
function initScene(panel) {
  const section=page.sections[Number(panel.closest('section').dataset.index)];
  const canvas=panel.querySelector('canvas'), ctx=canvas.getContext('2d');
  const status=panel.querySelector('.scene-status');
  if (!ctx) { status.textContent=words('3D drawing unavailable. Components and connections are listed below.', '3D недоступно. Компоненты и связи перечислены ниже.'); return; }
  const bounds=sceneBounds(section.objects), meshes=new Map(section.objects.map(o=>[o.id,sceneMesh(o.shape)]));
  const colors=['blue','green','orange','purple','cyan','pink'];
  let yaw=-.45, pitch=.28, zoom=1, pending=false, drag=null;
  const normalize=p=>p.map((v,i)=>(v-bounds.center[i])/bounds.radius);
  function schedule() { if (!pending) { pending=true; requestAnimationFrame(()=>{pending=false;draw();}); } }
  function draw() {
    const width=canvas.clientWidth, height=canvas.clientHeight;
    if (!width || !height) return;
    const dpr=Math.min(window.devicePixelRatio||1,2);
    if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);}
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
    const styles=getComputedStyle(document.documentElement), scale=Math.min(width,height)*.29*zoom;
    const project=p=>{const k=6/(6-p[2]);return [width/2+p[0]*scale*k,height/2-p[1]*scale*k];};
    const transformed=p=>rotatePoint(normalize(p),yaw,pitch);
    ctx.lineWidth=1;ctx.strokeStyle=styles.getPropertyValue('--grid');
    for(let i=-5;i<=5;i++){
      [[[-1.4,-1.25,i*.28],[1.4,-1.25,i*.28]],[[i*.28,-1.25,-1.4],[i*.28,-1.25,1.4]]].forEach(line=>{
        const a=project(rotatePoint(line[0],yaw,pitch)),b=project(rotatePoint(line[1],yaw,pitch));
        ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();
      });
    }
    const byId=new Map(section.objects.map(o=>[o.id,o]));
    const faces=[];
    section.objects.forEach((o,index)=>{
      const mesh=meshes.get(o.id),size=(o.size||1)*.5;
      const vertices=mesh.vertices.map(p=>transformed(p.map((v,i)=>o.position[i]+v*size)));
      const color=styles.getPropertyValue('--'+(o.color||colors[index%colors.length])).trim();
      mesh.faces.forEach(face=>{
        const points=face.map(i=>vertices[i]);
        faces.push({points,color,depth:points.reduce((sum,p)=>sum+p[2],0)/points.length});
      });
    });
    faces.sort((a,b)=>a.depth-b.depth);
    faces.forEach(face=>{
      const points=face.points.map(project);ctx.beginPath();ctx.moveTo(...points[0]);points.slice(1).forEach(p=>ctx.lineTo(...p));ctx.closePath();
      ctx.fillStyle=face.color;ctx.fill();
      const a=face.points[0],b=face.points[1],c=face.points[2],u=b.map((v,i)=>v-a[i]),v=c.map((n,i)=>n-a[i]);
      const normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
      const length=Math.hypot(...normal)||1,shade=.12+.32*(1-Math.abs((normal[0]*.3+normal[1]*.7+normal[2]*.6)/length));
      ctx.fillStyle=`rgba(0,0,0,${shade})`;ctx.fill();ctx.strokeStyle=face.color;ctx.lineWidth=.5;ctx.stroke();
    });
    ctx.lineWidth=2;ctx.strokeStyle=styles.getPropertyValue('--muted');ctx.fillStyle=ctx.strokeStyle;
    (section.edges||[]).forEach(edge=>{
      const a=project(transformed(byId.get(edge.from).position)),b=project(transformed(byId.get(edge.to).position));
      ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();
      const angle=Math.atan2(b[1]-a[1],b[0]-a[0]),mx=a[0]+(b[0]-a[0])*.65,my=a[1]+(b[1]-a[1])*.65;
      ctx.beginPath();ctx.moveTo(mx,my);ctx.lineTo(mx-10*Math.cos(angle-.45),my-10*Math.sin(angle-.45));ctx.lineTo(mx-10*Math.cos(angle+.45),my-10*Math.sin(angle+.45));ctx.closePath();ctx.fill();
    });
    ctx.font='14px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';
    const placed=[];
    section.objects.forEach(o=>{
      const point=project(transformed(o.position)),label=o.label.length>38?o.label.slice(0,35)+'…':o.label;
      const w=ctx.measureText(label).width+18;let y=point[1]+26;
      for(let attempt=0;attempt<8&&placed.some(p=>Math.abs(p[0]-point[0])<(p[2]+w)/2&&Math.abs(p[1]-y)<26);attempt++)y+=27;
      placed.push([point[0],y,w]);ctx.fillStyle=styles.getPropertyValue('--surface');ctx.fillRect(point[0]-w/2,y-13,w,26);
      ctx.fillStyle=styles.getPropertyValue('--text');ctx.fillText(label,point[0],y);
    });
  }
  function adjust(action) {
    if(action==='in')zoom=Math.min(3,zoom*1.15);
    if(action==='out')zoom=Math.max(.4,zoom/1.15);
    if(action==='left')yaw-=.15;
    if(action==='right')yaw+=.15;
    if(action==='up')pitch=Math.min(1.45,pitch+.15);
    if(action==='down')pitch=Math.max(-1.45,pitch-.15);
    if(action==='reset'){yaw=-.45;pitch=.28;zoom=1;}
    canvas.dataset.view=[yaw,pitch,zoom].map(v=>v.toFixed(3)).join(',');schedule();
  }
  panel.querySelectorAll('[data-turn]').forEach(b=>b.addEventListener('click',()=>adjust(b.dataset.turn)));
  panel.querySelectorAll('[data-zoom]').forEach(b=>b.addEventListener('click',()=>adjust(b.dataset.zoom)));
  panel.querySelector('[data-reset]').addEventListener('click',()=>adjust('reset'));
  canvas.addEventListener('keydown',event=>{
    const action={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down','+':'in','=':'in','-':'out',Home:'reset'}[event.key];
    if(action){event.preventDefault();adjust(action);}
  });
  canvas.addEventListener('pointerdown',event=>{if(event.button!==0)return;canvas.focus();canvas.setPointerCapture(event.pointerId);drag=[event.clientX,event.clientY];});
  canvas.addEventListener('pointermove',event=>{
    if(!drag)return;yaw+=(event.clientX-drag[0])*.008;pitch=Math.max(-1.45,Math.min(1.45,pitch+(event.clientY-drag[1])*.008));
    drag=[event.clientX,event.clientY];adjust('drag');
  });
  ['pointerup','pointercancel','lostpointercapture'].forEach(name=>canvas.addEventListener(name,()=>{drag=null;}));
  canvas.addEventListener('wheel',event=>{if(document.activeElement!==canvas)return;event.preventDefault();adjust(event.deltaY<0?'in':'out');},{passive:false});
  new ResizeObserver(schedule).observe(canvas);document.addEventListener('visual-theme',schedule);adjust('reset');
}
document.querySelectorAll('.scene').forEach(initScene);
