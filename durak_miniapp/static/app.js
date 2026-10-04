const tg = window.Telegram?.WebApp;
const state = { data:null, socket:null, selected:null, defendTarget:null };

if (tg) {
  tg.ready();
  tg.expand();
  try { tg.requestFullscreen?.(); } catch {}
}

function el(id){ return document.getElementById(id); }
function esc(s){ return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function cardHtml(c, cls=''){
  const red = c.suit === '♥' || c.suit === '♦';
  const r = ({11:'J',12:'Q',13:'K',14:'A'})[c.rank] || c.rank;
  return `<div class="card ${red?'red ':''}${cls}" data-card="${esc(c.rank+c.suit)}"><span class="rank">${r}</span><span class="suit">${c.suit}</span><span class="corner">${r}${c.suit}</span></div>`;
}
function getInitData(){ return tg?.initData || ''; }
function connect(){
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  state.socket = new WebSocket(`${proto}://${location.host}/ws?initData=${encodeURIComponent(getInitData())}`);
  state.socket.onopen = ()=>{ el('connectionText').textContent='Игра онлайн'; el('connectionDot').style.background='#56d364'; el('connectionDot').style.boxShadow='0 0 9px #56d364'; };
  state.socket.onclose = ()=>{ el('connectionText').textContent='Переподключение…'; el('connectionDot').style.background='#e6a33f'; setTimeout(connect,1200); };
  state.socket.onerror = ()=>{};
  state.socket.onmessage = ev => { const msg=JSON.parse(ev.data); if(msg.error){ showModal('Ошибка',msg.error); return;} state.data=msg; render(); };
}
function send(action, extra={}){ if(state.socket?.readyState===1) state.socket.send(JSON.stringify({action,...extra})); }

function render(){
  const d=state.data; if(!d) return;
  el('deckCount').textContent=d.deck_count;
  el('message').textContent=d.message||'';
  el('event').textContent=d.last_event||'';
  el('trumpCard').innerHTML = d.trump ? `<span style="font-size:32px">${d.trump}</span>` : '—';
  renderPlayers(d);
  renderTable(d);
  renderHand(d);
  renderActions(d);
  if(d.phase==='finished') showModal('Партия окончена', d.message);
}
function renderPlayers(d){
  const others=d.players.filter(p=>p.id!==d.me); const me=d.players.find(p=>p.id===d.me);
  let list=[]; if(me) list.push({...me,slot:0}); if(others[0]) list.push({...others[0],slot:1}); if(others[1]) list.push({...others[1],slot:2});
  el('players').innerHTML=list.map(p=>`<div class="player p${p.slot} ${((p.role==='attacker') || (p.role==='defender'))?'active':''}"><div class="avatar">${esc((p.name||'?').slice(0,1).toUpperCase())}</div><div class="name">${esc(p.name)}${p.id===d.me?' · вы':''}</div><div class="count">${p.count} карт · ${p.role==='attacker'?'атака':p.role==='defender'?'защита':'ожидает'}</div></div>`).join('');
}
function renderTable(d){
  el('tableCards').innerHTML = d.table.map((pair,i)=>`<div class="pair" data-target="${i}">${cardHtml(pair.attack,'attack')}${pair.defense?cardHtml(pair.defense,'defense'):''}</div>`).join('');
  if(d.can_defend){ document.querySelectorAll('.pair').forEach(x=>x.addEventListener('click',()=>{ state.defendTarget=Number(x.dataset.target); updateHint(); })); }
}
function renderHand(d){
  const hand=d.hand||[];
  el('hand').innerHTML=hand.map((c,i)=>{ const sel=state.selected===c.rank+c.suit?'selected':''; return cardHtml(c,sel); }).join('');
  document.querySelectorAll('#hand .card').forEach(node=>node.addEventListener('click',()=>{
    const cid=node.dataset.card; state.selected=state.selected===cid?null:cid; updateHint(); renderHand(d);
  }));
}
function renderActions(d){
  const a=el('actions'); a.innerHTML='';
  if(d.phase==='lobby'){
    if(d.players.length<3) {
      const start=document.createElement('button'); start.className='action'; start.textContent=d.players.length>=2?'▶ Начать игру':'Ждём второго игрока…'; start.disabled=d.players.length<2 || d.me!==d.players[0].id; start.onclick=()=>send('start'); a.appendChild(start);
    }
  }
  if(d.phase==='playing'){
    if(d.can_attack){
      const attack=document.createElement('button'); attack.className='action'; attack.textContent='⚔️ Атаковать'; attack.disabled=!state.selected; attack.onclick=()=>{send('attack',{card:state.selected});state.selected=null}; a.appendChild(attack);
      const pass=document.createElement('button'); pass.className='action secondary'; pass.textContent='Отбой'; pass.disabled=d.open_attacks_count; pass.onclick=()=>send('pass'); a.appendChild(pass);
    }
    if(d.can_defend){
      const def=document.createElement('button'); def.className='action'; def.textContent='🛡️ Отбиться'; def.disabled=!state.selected || state.defendTarget===null; def.onclick=()=>{send('defend',{card:state.selected,target:state.defendTarget});state.selected=null;state.defendTarget=null}; a.appendChild(def);
      const take=document.createElement('button'); take.className='action danger'; take.textContent='✋ Забрать'; take.onclick=()=>{send('take');state.selected=null;state.defendTarget=null}; a.appendChild(take);
    }
  }
  updateHint();
}
function updateHint(){
  const d=state.data; if(!d) return;
  if(d.phase==='lobby') el('hint').textContent='Откройте игру в группе и подключите друзей';
  else if(d.can_attack) el('hint').textContent=state.selected?'Готово к атаке':'Выберите карту для атаки';
  else if(d.can_defend) el('hint').textContent=state.defendTarget!==null?'Теперь выберите карту для защиты':'Сначала выберите карту на столе';
  else el('hint').textContent='Сейчас ход соперника';
}
function showModal(title,text){ el('modalTitle').textContent=title; el('modalText').textContent=text; el('modal').classList.remove('hidden'); }
el('modalButton').onclick=()=>el('modal').classList.add('hidden');
connect();
