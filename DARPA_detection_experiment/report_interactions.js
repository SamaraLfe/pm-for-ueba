function showModel(){
  const selected=document.querySelector('#model-select').value;
  document.querySelectorAll('.model-panel').forEach(panel=>panel.hidden=panel.dataset.model!==selected);
}
function resizeStage(panel,change){
  const stage=panel.querySelector('.net-stage');
  if(panel.classList.contains('explanation')){
    const base=Number(stage.dataset.baseWidth);
    const current=Number(stage.dataset.zoom||100);
    const next=change===0?(panel.querySelector('.network').clientWidth-26)/base*100:Math.max(20,Math.min(400,current+change));
    stage.dataset.zoom=next;stage.style.width=(base*next/100)+'px';
    return;
  }
  const current=Number(stage.dataset.zoom||100);
  const next=change===0?100:Math.max(50,Math.min(400,current+change));
  stage.dataset.zoom=next;stage.style.width=next+'%';
}
function zoomModel(change){resizeStage(document.querySelector('.model-panel:not([hidden])'),change)}
function zoomExample(change){resizeStage(document.querySelector('.explanation:not([hidden])'),change)}
function openModel(){
  const dialog=document.querySelector('#model-dialog');
  dialog.querySelector('div').innerHTML=document.querySelector('.model-panel:not([hidden]) svg').outerHTML;
  dialog.showModal();
}
function openExample(){
  const dialog=document.querySelector('#model-dialog');
  dialog.querySelector('div').innerHTML=document.querySelector('.explanation:not([hidden]) svg').outerHTML;
  dialog.showModal();
}
function showExample(){
  const index=Number(document.querySelector('#example-select').value);
  document.querySelectorAll('.explanation').forEach(panel=>panel.hidden=Number(panel.dataset.example)!==index);
  const panel=document.querySelector('.explanation:not([hidden])');
  const stage=panel.querySelector('.net-stage');
  if(!stage.dataset.baseWidth){
    const base=Math.max(1500,panel.querySelector('svg').viewBox.baseVal.width);
    stage.dataset.baseWidth=base;stage.style.width=base+'px';
  }
  const moves=explanationData[index].moves;
  const first=moves.findIndex(move=>move.kind==='log'||move.kind==='model');
  selectStep(index,first<0?0:first);
}
function selectStep(exampleIndex,stepIndex){
  const panel=document.querySelector(`.explanation[data-example="${exampleIndex}"]`);
  const move=explanationData[exampleIndex].moves[stepIndex];
  if(!move)return;
  panel.querySelectorAll('.move-row').forEach(row=>row.classList.toggle('selected',Number(row.dataset.step)===stepIndex));
  const svg=panel.querySelector('svg');
  svg.querySelectorAll('.mark-token').forEach(node=>node.remove());
  svg.querySelectorAll('[data-place]').forEach(group=>{
    group.querySelectorAll('text').forEach(text=>text.style.display='none');
    const count=move.before[group.dataset.place]||0;
    const circle=group.querySelector('ellipse,circle');
    if(count&&circle){
      const text=document.createElementNS('http://www.w3.org/2000/svg','text');
      text.setAttribute('x',circle.getAttribute('cx'));text.setAttribute('y',Number(circle.getAttribute('cy'))+4);
      text.setAttribute('text-anchor','middle');text.setAttribute('class','mark-token');
      text.textContent=count;group.append(text);
    }
  });
  svg.querySelectorAll('[data-transition]').forEach(group=>{
    group.querySelectorAll('polygon,rect,path').forEach(shape=>{
      if(!shape.dataset.originalStroke){shape.dataset.originalStroke=shape.getAttribute('stroke')||'black'}
      const active=group.dataset.transition===move.transition;
      shape.setAttribute('stroke',active?'#20679a':shape.dataset.originalStroke);
      shape.setAttribute('stroke-width',active?'3':'1');
    });
  });
  const selectedTransition=[...svg.querySelectorAll('[data-transition]')].find(n=>n.dataset.transition===move.transition);
  const logNode=move.kind==='log'?[...svg.querySelectorAll('g.node')].find(n=>[...n.querySelectorAll('text')].some(t=>t.textContent.startsWith(move.log_action+' ×'))):null;
  const focusNode=selectedTransition||logNode;
  if(focusNode){
    const viewport=panel.querySelector('.network');
    const target=focusNode.getBoundingClientRect();
    const container=viewport.getBoundingClientRect();
    viewport.scrollLeft+=target.left-container.left+target.width/2-viewport.clientWidth/2;
    viewport.scrollTop+=target.top-container.top+target.height/2-viewport.clientHeight/2;
  }
  const explanations={
    sync:`Действие ${move.log_action} совпадает с разрешённым переходом сети.`,
    tau:'Внутренний переход сети без наблюдаемого действия.',
    log:`Действие ${move.log_action} не сопоставлено переходу на этом пути. Маркировка не меняется.`,
    model:`Переход ${move.model_action} выполняется без соответствующего действия журнала.`
  };
  panel.querySelector('.step-status').textContent=`Шаг ${stepIndex+1}. ${explanations[move.kind]} Показаны токены перед шагом.`;
}
document.querySelector('#model-select').addEventListener('change',showModel);
document.querySelector('#example-select').addEventListener('change',showExample);
document.querySelector('#show-tau').addEventListener('change',event=>{
  document.querySelectorAll('.explanation').forEach(panel=>panel.classList.toggle('show-tau',event.target.checked));
  showExample();
});
document.querySelector('#show-all-moves').addEventListener('change',event=>{
  document.querySelectorAll('.explanation').forEach(panel=>panel.classList.toggle('show-all-moves',event.target.checked));
  showExample();
});
showModel();showExample();
