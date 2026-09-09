/* 研发缺陷协作系统 · 前端逻辑（独立文件）。
   修复记录：
   - 栏目切换：非当前页 display:'none'（此前误写 'grid' 导致所有区块铺开成一条龙）
   - saveFields：请求带 role，否则后端不返回 can_edit，字段修改静默失败
   - 优先级筛选：innerHTML 覆盖而非追加，避免出现两个「全部」
   - 状态流转：用确认弹窗替代原生 prompt（可填备注）
*/
const API="";
const $=s=>document.querySelector(s);
const SVG=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 280" font-family="-apple-system,Segoe UI,Microsoft YaHei,sans-serif">
  <defs><marker id="ah" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 Z" fill="#555"/></marker></defs>
  <style>.box{fill:#eef4ff;stroke:#3b82f6;stroke-width:1.6;rx:8}.init{fill:#e7f7ee;stroke:#10b981;stroke-width:1.8}.rej{fill:#f1f3f5;stroke:#adb5bd;stroke-width:1.6}.reo{fill:#fff4e6;stroke:#f59e0b;stroke-width:1.6}.lbl{font-size:13px;fill:#1f2937;font-weight:600;text-anchor:middle}.sub{font-size:10px;fill:#6b7280;text-anchor:middle}.edge{fill:none;stroke:#555;stroke-width:1.5;marker-end:url(#ah)}.erole{font-size:10px;fill:#9b3b00;text-anchor:middle;font-weight:600}</style>
  <path class="edge" d="M160,63 L206,63"/><path class="edge" d="M95,86 C95,150 250,150 272,198"/><path class="edge" d="M340,63 L386,63"/><path class="edge" d="M520,63 L566,63"/><path class="edge" d="M700,63 L746,63"/><path class="edge" d="M635,86 L635,196"/><path class="edge" d="M635,200 C635,140 460,140 460,88"/>
  <text class="erole" x="184" y="54">产品·确认</text><text class="erole" x="150" y="165">产品·拒绝</text><text class="erole" x="364" y="54">开发·开始修复</text><text class="erole" x="544" y="54">开发·提交修复</text><text class="erole" x="724" y="54">测试·验证通过</text><text class="erole" x="652" y="150">测试·验证不通过</text><text class="erole" x="540" y="170">开发·重新修复</text>
  <rect class="box init" x="30" y="40" width="130" height="46" rx="8"/><text class="lbl" x="95" y="63">新建</text><text class="sub" x="95" y="78">测试提交</text>
  <rect class="box rej" x="210" y="200" width="130" height="46" rx="8"/><text class="lbl" x="275" y="223">已拒绝</text><text class="sub" x="275" y="238">终态</text>
  <rect class="box" x="210" y="40" width="130" height="46" rx="8"/><text class="lbl" x="275" y="63">已确认</text><text class="sub" x="275" y="78">产品指派</text>
  <rect class="box" x="390" y="40" width="130" height="46" rx="8"/><text class="lbl" x="455" y="63">修复中</text><text class="sub" x="455" y="78">开发修复</text>
  <rect class="box" x="570" y="40" width="130" height="46" rx="8"/><text class="lbl" x="635" y="63">待验证</text><text class="sub" x="635" y="78">等测试复测</text>
  <rect class="box reo" x="570" y="200" width="130" height="46" rx="8"/><text class="lbl" x="635" y="223">重新打开</text><text class="sub" x="635" y="238">复测失败</text>
  <rect class="box term" x="750" y="40" width="130" height="46" rx="8"/><text class="lbl" x="815" y="63">已关闭</text><text class="sub" x="815" y="78">终态</text>
</svg>`;

let META=null, role="测试", live=true, timer=null, lastSync="";
let lastMaxEventId=null;      // 已见到的最新 history id（用于增量检测新动态）
let curUpdatedAt=null;        // 打开详情时的 updated_at（乐观锁基准）
let notifyOn = localStorage.getItem('defect_notify')==='1';
let curBugId=null;        // 当前打开的详情抽屉
let pendingTr=null;       // 待确认的流转 {id,to,label}
const me=()=>$('#who').value.trim()||role;   // 操作人姓名：留痕到每条记录
function loadWho(){ $('#who').value=localStorage.getItem('defect_name_'+role)||''; }
function saveWho(){ localStorage.setItem('defect_name_'+role,$('#who').value.trim()); }
function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),2200);}
async function api(path,opt){opt=opt||{};opt.headers=Object.assign({'Content-Type':'application/json'},opt.headers||{});const tk=localStorage.getItem('defect_token');if(tk)opt.headers['Authorization']='Bearer '+tk;const r=await fetch(API+path,opt);if(r.status===401){localStorage.removeItem('defect_token');location.href='/';return;}const j=await r.json().catch(()=>({}));if(!r.ok)throw Object.assign(new Error(j.detail||'错误'),{status:r.status,j});return j;}

async function loadMeta(){
  META=await api('/api/meta');
  $('#fStatus').innerHTML='<option value="">全部</option>'+META.states.map(s=>`<option>${s}</option>`).join('');
  $('#fPri').innerHTML='<option value="">全部</option>'+META.priorities.map(p=>`<option>${p}</option>`).join('');
  $('#fProj').innerHTML='<option value="">全部</option>'+(META.projects||[]).map(p=>`<option>${esc(p)}</option>`).join('');
  $('#nProj').innerHTML=(META.projects||[META.default_project]).map(p=>`<option>${esc(p)}</option>`).join('');
  $('#diagram').innerHTML=SVG;
  $('#respList').innerHTML=META.roles.map(r=>`<div class="resp"><b>${r}</b>：${META.responsibilities[r]}</div>`).join('');
  $('#cfgSec').textContent=META.config.auto_refresh_sec;
}

// ── 新动态通知（浏览器 Notification，不依赖任何外部服务）──
function updateNotifyBtn(){const b=$('#btnNotify');b.textContent=notifyOn?'🔔 已开启':'🔔 提醒';b.style.opacity=notifyOn?'1':'.75';}
async function toggleNotify(){
  if(notifyOn){notifyOn=false;localStorage.setItem('defect_notify','0');updateNotifyBtn();toast('已关闭新动态提醒');return;}
  if(!('Notification' in window)){toast('⚠️ 此浏览器不支持系统通知');return;}
  let perm=Notification.permission;
  if(perm==='default') perm=await Notification.requestPermission();
  if(perm!=='granted'){toast('⚠️ 浏览器未授权通知，请在地址栏权限里允许');return;}
  notifyOn=true;localStorage.setItem('defect_notify','1');updateNotifyBtn();
  toast('✅ 已开启：他人推进缺陷时会弹系统通知');
}
function notifyNewEvents(events){
  if(!notifyOn||typeof Notification==='undefined'||Notification.permission!=='granted')return;
  events.slice(-3).forEach(e=>{
    let body='';
    if(e.kind==='status')body=`状态 ${e.from_status||'∅'} → ${e.to_status}`;
    else if(e.kind==='field')body=`修改 ${e.field}：${e.old_val||'∅'} → ${e.new_val||'∅'}`;
    else if(e.kind==='file')body=e.comment;
    else body=e.comment||'';
    try{const n=new Notification(`缺陷 #${e.bug_id} 有新动态`,{body:`${e.actor||e.by_role}：${body}`,tag:'defect-'+e.bug_id});
        n.onclick=()=>{window.focus();openBug(e.bug_id);n.close();};}catch(_){}
  });
}

// ── 设计思路页：权限矩阵由后端声明实时渲染 ──
const FIELD_LABEL={'priority':'优先级','severity':'严重程度','assignee':'指派处理人','blocked':'阻塞标记','title':'标题','description':'描述'};
const ACTION_LABEL={'create_bug':'新建缺陷','comment':'评论协作','reject':'拒绝缺陷','close':'关闭缺陷','reopen':'重新打开'};
function renderIdea(){
  if(!META)return;
  const R=META.roles, mark=arr=>R.map(r=>arr.includes(r)?'<span class="y">✓</span>':'<span class="n">—</span>').join('');
  const tbl=(title,rows)=>`<table class="mx"><thead><tr><th style="text-align:left">${title}</th>${R.map(r=>`<th>${r}</th>`).join('')}</tr></thead><tbody>${
    rows.map(([lab,arr])=>`<tr><td class="lab">${lab}</td>${mark(arr)}</tr>`).join('')}</tbody></table>`;
  const fRows=Object.entries(META.field_edit_roles||{}).map(([k,v])=>[FIELD_LABEL[k]||k,v]);
  const aRows=Object.entries(META.action_roles||{}).map(([k,v])=>[ACTION_LABEL[k]||k,v]);
  const tRows=(META.transitions||[]).map(t=>[`${t.from} → ${t.to}（${t.action}）`,[t.role]]);
  $('#mxField').innerHTML=tbl('可修改字段',fRows);
  $('#mxAction').innerHTML=tbl('可执行动作',aRows)+tbl('状态流转',tRows);
  const c=META.config||{};
  $('#cfgFix').textContent=c.sla_fix_hours??'-';
  $('#cfgVerify').textContent=c.sla_verify_hours??'-';
  $('#cfgReopen').textContent=c.reopen_limit??'-';
  $('#cfgSec2').textContent=c.auto_refresh_sec??'-';
  $('#cfgChips').innerHTML=Object.entries(c).map(([k,v])=>`<span class="chip">${k} = <b>${v}</b></span>`).join('');
}

function renderBars(el,obj,total,colors){
  const max=Math.max(1,...Object.values(obj));
  el.innerHTML=Object.entries(obj).map(([k,v])=>{
    const c=colors&&colors[k]||'var(--blue)';
    return `<div class="bar"><span class="lab">${esc(k)}</span><span class="track"><span class="fill" style="width:${v/max*100}%;background:${c}"></span></span><span class="val">${v}</span></div>`;
  }).join('')||'<div class="empty">暂无数据</div>';
}

async function loadDash(){
  const d=await api('/api/dashboard');
  $('#kTotal').textContent=d.total;$('#kOpen').textContent=d.open;$('#kClosed').textContent=d.closed;$('#kRisk').textContent=d.risk_list.length;
  $('#kClosed').nextElementSibling.textContent=d.avg_fix_hours!=null?`已关闭 · 平均 ${d.avg_fix_hours}h 解决`:'已关闭';
  const stC={'新建':'#10b981','已确认':'#3b82f6','已拒绝':'#9ca3af','修复中':'#f59e0b','待验证':'#8b5cf6','已关闭':'#10b981','重新打开':'#ef4444'};
  renderBars($('#byStatus'),d.by_status,d.total,stC);
  renderBars($('#byPri'),d.by_priority,d.total);
  renderBars($('#byAssignee'),d.by_assignee,d.total);
  $('#riskList').innerHTML=d.risk_list.length?d.risk_list.map(r=>`<div class="bug" onclick="openBug(${r.id})" style="cursor:pointer"><div class="top"><span class="tag st-${r.status}">${r.status}</span><span class="title">#${r.id} ${esc(r.title)}</span></div><div class="risk">${r.risks.map(x=>`<span class="r">${esc(x)}</span>`).join('')}</div></div>`).join(''):'<div class="empty">暂无风险项 🎉</div>';
  // 三角色共同进度：各自队列 + 职责
  const icons={'测试':'🧪','产品':'🧭','开发':'🛠'};
  $('#roleCards').innerHTML=Object.entries(d.role_progress||{}).map(([r,p])=>{
    const total=Object.values(p.queues).reduce((a,b)=>a+b,0);
    const rows=Object.entries(p.queues).map(([s,n])=>`<div class="bar"><span class="lab">${esc(s)}</span><span class="track"><span class="fill" style="width:${total?n/total*100:0}%;background:var(--blue)"></span></span><span class="val">${n}</span></div>`).join('');
    return `<div class="card"><h2>${icons[r]||''} ${r} 的进度（${total}）</h2><div class="resp"><b>职责：</b>${esc(p.responsibility)}</div>${rows||'<div class="empty">—</div>'}</div>`;
  }).join('');
}

async function loadFeed(){
  const a=await api('/api/activity?limit=40');
  // 增量检测新动态（他人操作才提醒），并弹系统通知
  if(a.events.length){
    const maxId=Math.max(...a.events.map(e=>e.id));
    if(lastMaxEventId!=null&&maxId>lastMaxEventId){
      const fresh=a.events.filter(e=>e.id>lastMaxEventId&&e.actor!==me()&&e.kind!=='comment');
      if(fresh.length){notifyNewEvents(fresh);toast(`⚡ ${fresh.length} 条新动态（${esc(fresh[0].actor||fresh[0].by_role)} 等）`);}
    }
    lastMaxEventId=Math.max(lastMaxEventId??0,maxId);
  }
  $('#feed').innerHTML=a.events.length?a.events.slice().reverse().map(e=>{
    let body='';
    if(e.kind==='status') body=`状态 ${esc(e.from_status||'∅')} → <b>${esc(e.to_status)}</b>`;
    else if(e.kind==='field') body=`修改 <b>${esc(e.field)}</b>：${esc(e.old_val||'∅')} → ${esc(e.new_val||'∅')}`;
    else if(e.kind==='comment') body=`评论：${esc(e.comment)}`;
    else if(e.kind==='system') body=`<span class="sysEv">🛡 ${esc(e.comment)}</span>`;
    else if(e.kind==='file') body=`📎 ${esc(e.comment)}`;
    else body=esc(e.comment||'');
    return `<div class="ev"><span class="t">${e.created_at.slice(5)}</span><span><span class="who role-${e.by_role}">${esc(e.actor||e.by_role)}</span> ${body}</span></div>`;
  }).join(''):'<div class="empty">暂无活动</div>';
}

let listPage=1;const PAGE_SIZE=20;
async function loadList(keepPage){
  if(!keepPage)listPage=1;
  const st=$('#fStatus').value,pri=$('#fPri').value,asg=$('#fAssignee').value,risk=$('#fRisk').checked?1:0,proj=$('#fProj').value;
  const kw=($('#fSearch').value||'').trim();
  let url=`/api/bugs?role=${encodeURIComponent(role)}&risk=${risk}&limit=${PAGE_SIZE}&offset=${(listPage-1)*PAGE_SIZE}`;
  if(kw)url+=`&q=${encodeURIComponent(kw)}`;
  if(st)url+=`&status=${encodeURIComponent(st)}`;if(pri)url+=`&priority=${encodeURIComponent(pri)}`;
  if(proj)url+=`&project=${encodeURIComponent(proj)}`;if(asg)url+=`&assignee=${encodeURIComponent(asg)}`;
  const d=await api(url);
  const total=d.total??d.bugs.length,pages=Math.max(1,Math.ceil(total/PAGE_SIZE));
  $('#list').innerHTML=d.bugs.length?d.bugs.map(bugCard).join(''):'<div class="empty">无匹配缺陷</div>';
  bindActions($('#list'));
  $('#pager').innerHTML=pages>1?`<button class="act ghost" ${listPage<=1?'disabled':''} onclick="listPage--;loadList(true)">← 上一页</button>
    <span style="font-size:12px;color:var(--muted)">共 ${total} 条 · 第 ${listPage}/${pages} 页</span>
    <button class="act ghost" ${listPage>=pages?'disabled':''} onclick="listPage++;loadList(true)">下一页 →</button>`
    :(total>PAGE_SIZE?'':`<span style="font-size:12px;color:var(--muted)">共 ${total} 条</span>`);
}

function bugCard(b){
  const risks=(b.risks||[]).map(x=>`<span class="r">${esc(x)}</span>`).join('');
  const acts=(b.actions||[]).map(a=>`<button class="act" data-id="${b.id}" data-to="${a.to}" data-label="${a.action} → ${a.to}" data-act="tr">${a.action} → ${a.to}</button>`).join('');
  const blk=b.blocked?`<span class="blocked">⛔ 阻塞${b.blocked_reason?'：'+esc(b.blocked_reason):''}</span>`:'';
  return `<div class="bug" onclick="openBug(${b.id})">
    <div class="top"><span class="tag st-${b.status}">${b.status}</span><span class="title">#${b.id} ${esc(b.title)}</span>
      <span class="tag pri-${b.priority}">${b.priority}</span></div>
    <div class="meta"><span>项目：${esc(b.project||META?.default_project||'默认项目')}</span><span>严重：${esc(b.severity)}</span><span>报告：${esc(b.reporter)}</span><span>指派：${esc(b.assignee||'—')}</span><span>重开×${b.reopen_count}</span></div>
    ${blk?`<div class="meta">${blk}</div>`:''}${risks?`<div class="risk">${risks}</div>`:''}
    ${acts?`<div class="row" onclick="event.stopPropagation()">${acts}</div>`:''}
  </div>`;
}

function bindActions(scope){
  scope.querySelectorAll('button[data-act="tr"]').forEach(btn=>{
    btn.onclick=()=>openTransition(btn.dataset.id,btn.dataset.to,btn.dataset.label);
  });
}

// 我的待办：按角色职责过滤（后端 _is_my_todo 判定，含按姓名匹配指派）
async function loadMine(){
  const d=await api(`/api/bugs?role=${encodeURIComponent(role)}&my=${encodeURIComponent(me())}`);
  const list=d.bugs;
  $('#mineTitle').textContent=`我的待办 · ${role}${$('#who').value.trim()?'（'+$('#who').value.trim()+'）':''}（${list.length}）`;
  $('#mine').innerHTML=list.length?list.map(bugCard).join(''):'<div class="empty">当前没有待你处理的事项 🎉</div>';
  bindActions($('#mine'));
}

// 详情抽屉
async function openBug(id){
  let d;try{d=await api(`/api/bugs/${id}?role=${encodeURIComponent(role)}`);}catch(e){toast('⚠️ '+e.message);return;}
  const b=d.bug;curBugId=id;curUpdatedAt=b.updated_at;
  $('#dTitle').textContent=`#${b.id} ${b.title}`;
  const canEdit=b.can_edit||{};
  let html=`<div class="sec">
    <div class="row"><span class="tag st-${b.status}">${b.status}</span><span class="tag pri-${b.priority}">${b.priority}</span><span class="tag">严重：${esc(b.severity)}</span><span class="tag">📁 ${esc(b.project||META?.default_project||'默认项目')}</span>${b.blocked?`<span class="blocked">⛔ 阻塞</span>`:''}</div>
    <div class="meta">报告人：${esc(b.reporter)} ｜ 指派：${esc(b.assignee||'未指派')} ｜ 重开×${b.reopen_count} ｜ 更新：${b.updated_at}</div>
    <div class="meta">${esc(b.description||'（无描述）')}</div>
    ${(b.risks||[]).length?`<div class="risk">${b.risks.map(x=>`<span class="r">${esc(x)}</span>`).join('')}</div>`:''}
  </div>`;
  // 标题 / 描述（任何角色可编辑，方便补充信息）
  html+=`<div class="sec"><h4>标题 / 描述（可直接修改）</h4>`;
  html+=`<input id="eTitle" value="${esc(b.title)}" style="width:100%;font-size:15px;font-weight:600;margin-bottom:8px"/>`;
  html+=`<textarea id="eDesc" rows="4" style="width:100%;resize:vertical" placeholder="补充描述：复现步骤 / 期望结果 / 实际结果">${esc(b.description||'')}</textarea>`;
  html+=`<div class="row" style="margin-top:8px"><button class="act" onclick="saveText(${b.id})">保存标题/描述</button></div></div>`;
  // 字段编辑（按角色权限）
  html+=`<div class="sec"><h4>字段（按角色权限）</h4><div class="row">`;
  if(canEdit.priority) html+=`优先级<select id="ePri">${META.priorities.map(p=>`<option ${p===b.priority?'selected':''}>${p}</option>`).join('')}</select>`;
  if(canEdit.severity) html+=`严重程度<select id="eSev">${META.severities.map(p=>`<option ${p===b.severity?'selected':''}>${p}</option>`).join('')}</select>`;
  if(canEdit.assignee) html+=`指派<input id="eAsg" value="${esc(b.assignee||'')}" placeholder="处理人"/>`;
  if(canEdit.project) html+=`项目<select id="eProj">${[...new Set([...(META.projects||[]),b.project||META.default_project])].map(p=>`<option ${p===(b.project||META.default_project)?'selected':''}>${esc(p)}</option>`).join('')}</select>`;
  html+=`</div>`;
  if(canEdit.blocked) html+=`<div class="row">阻塞<select id="eBlk"><option value="0" ${!b.blocked?'selected':''}>否</option><option value="1" ${b.blocked?'selected':''}>是</option></select><input id="eBlkR" placeholder="阻塞原因" value="${esc(b.blocked_reason||'')}"/></div>`;
  if(Object.values(canEdit).some(Boolean)) html+=`<button class="act" onclick="saveFields(${b.id})">保存字段修改</button>`;
  else html+=`<div class="empty">当前身份无可修改字段</div>`;
  html+=`</div>`;
  // 流转
  const acts=b.actions||[];
  html+=`<div class="sec"><h4>状态流转</h4><div class="row">${acts.length?acts.map(a=>`<button class="act" onclick="openTransition(${b.id},'${a.to}','${a.action} → ${a.to}')">${a.action} → ${a.to}</button>`).join(''):'<span class="empty">当前身份无可执行流转</span>'}</div></div>`;
  // 历史时间线
  html+=`<div class="sec"><h4>可追溯记录</h4><div class="tl">`+d.history.map(h=>{
    let c='';if(h.kind==='status')c=`${esc(h.from_status||'∅')} → <b>${esc(h.to_status)}</b>`;
    else if(h.kind==='field')c=`修改 <b>${esc(h.field)}</b>：${esc(h.old_val||'∅')} → ${esc(h.new_val||'∅')}`;
    else if(h.kind==='comment')c=`评论：${esc(h.comment)}`;
    else if(h.kind==='system')c=`<span class="sysEv">🛡 ${esc(h.comment)}</span>`;
    else if(h.kind==='file')c=`📎 ${esc(h.comment)}`;
    else c=esc(h.comment||'');
    return `<div class="item ${h.kind}"><span class="who role-${h.by_role}">${esc(h.actor||h.by_role)}</span> <span class="role-${h.by_role}">[${h.by_role}]</span> ${c} <span style="color:#9ca3af">· ${h.created_at}</span></div>`;
  }).join('')+`</div></div>`;
  // 附件 / 截图（选择文件或直接 Ctrl+V 粘贴截图）
  const atts=d.attachments||[];
  html+=`<div class="sec"><h4>附件 / 截图（≤2MB，可直接粘贴截图）</h4>`;
  if(atts.length) html+=`<div class="row">`+atts.map(a=>`<a href="/api/attachments/${a.id}" target="_blank" title="${esc(a.filename)} · ${esc(a.actor||'')} ${esc(a.created_at)}"><img src="/api/attachments/${a.id}" style="width:64px;height:64px;object-fit:cover;border:1px solid var(--line);border-radius:8px"/></a>`).join('')+`</div>`;
  html+=`<div class="row"><input type="file" id="eFile" accept="image/*" style="font-size:12px"/><button class="act ghost" onclick="uploadFile(${b.id})">上传</button></div></div>`;
  // 评论
  html+=`<div class="sec"><h4>添加评论（三方协作）</h4><div class="row"><input id="eCmt" style="flex:1" placeholder="以【${role}·${esc(me())}】身份评论…（也可直接 Ctrl+V 粘贴截图）"/><button class="act" onclick="addComment(${b.id})">发送</button></div></div>`;
  // 终局操作：仅产品可删除，删除进审计日志（audit_log 永久保留）
  if(role==='产品') html+=`<div class="sec"><h4>终局操作（仅产品 · 删除会写入审计日志）</h4><button class="act" style="background:var(--red)" onclick="deleteBug(${b.id})">🗑 删除该缺陷</button></div>`;
  $('#dBody').innerHTML=html;
  $('#mask').classList.add('show');$('#drawer').classList.add('show');
}
async function saveText(id){
  const t=$('#eTitle').value.trim(),dsc=$('#eDesc').value;
  if(!t){toast('⚠️ 标题不能为空');return;}
  try{
    const r1=await api(`/api/bugs/${id}`,{method:'PATCH',body:JSON.stringify({field:'title',value:t,expect_updated_at:curUpdatedAt,by_role:role,actor:me()})});
    curUpdatedAt=r1.updated_at;  // 第一笔成功后刷新基准，第二笔才不会误判冲突
    const r2=await api(`/api/bugs/${id}`,{method:'PATCH',body:JSON.stringify({field:'description',value:dsc,expect_updated_at:curUpdatedAt,by_role:role,actor:me()})});
    curUpdatedAt=r2.updated_at;
    toast('✅ 标题/描述已保存');await openBug(id);await refreshAll();
  }catch(e){await handleConflict(e,id);}
}

async function saveFields(id){
  // 必须带 role，后端才会返回 can_edit（此前漏了导致字段保存静默失败）
  const b=await api(`/api/bugs/${id}?role=${encodeURIComponent(role)}`);const cur=b.bug;
  try{
    if(cur.can_edit.priority){const v=$('#ePri').value;if(v!==cur.priority)await api(`/api/bugs/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:'priority',value:v,expect_updated_at:curUpdatedAt,by_role:role,actor:me()})});}
    if(cur.can_edit.severity){const v=$('#eSev').value;if(v!==cur.severity)await api(`/api/bugs/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:'severity',value:v,expect_updated_at:curUpdatedAt,by_role:role,actor:me()})});}
    if(cur.can_edit.assignee){const v=$('#eAsg').value.trim();if(v!==(cur.assignee||''))await api(`/api/bugs/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:'assignee',value:v,expect_updated_at:curUpdatedAt,by_role:role,actor:me()})});}
    if(cur.can_edit.project){const v=$('#eProj').value;if(v!==(cur.project||META.default_project))await api(`/api/bugs/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:'project',value:v,expect_updated_at:curUpdatedAt,by_role:role,actor:me()})});}
    if(cur.can_edit.blocked){const v=$('#eBlk').value;const r=$('#eBlkR').value.trim();if((v==='1')!==!!cur.blocked)await api(`/api/bugs/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:'blocked',value:v,blocked_reason:r,expect_updated_at:curUpdatedAt,by_role:role,actor:me()})});}
    toast('字段已更新');refreshAll();openBug(id);
  }catch(e){await handleConflict(e,id);}
}
// 编辑冲突统一处理：409 提示刷新，其余报错透出
async function handleConflict(e,id){
  if(e.status===409){toast('⚠️ '+e.message);await openBug(id);}
  else toast('⚠️ '+(e.message||'保存失败'));
}

// ── 附件 / 粘贴截图 ──
async function sendAttachment(id,filename,dataUrl){
  const base64=dataUrl.split(',')[1];
  try{
    await api(`/api/bugs/${id}/attachments`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({filename,mime:dataUrl.slice(5,dataUrl.indexOf(';')),data:base64,by_role:role,actor:me()})});
    toast('📎 附件已上传');refreshAll();openBug(id);
  }catch(e){toast('⚠️ '+e.message);}
}
async function uploadFile(id){
  const f=$('#eFile').files[0];
  if(!f){toast('请先选择文件');return;}
  if(f.size>2*1024*1024){toast('⚠️ 超过 2MB 上限，请压缩后重试');return;}
  const rd=new FileReader();
  rd.onload=()=>sendAttachment(id,f.name,rd.result);
  rd.readAsDataURL(f);
}
async function uploadPaste(e){
  if(!curBugId)return;
  const items=e.clipboardData?.items||[];
  for(const it of items){
    if(it.type.startsWith('image/')){
      e.preventDefault();
      const f=it.getAsFile();
      const rd=new FileReader();
      rd.onload=()=>sendAttachment(curBugId,`截图_${new Date().toLocaleString('zh-CN',{hour12:false}).replace(/[/: ]/g,'')}.png`,rd.result);
      rd.readAsDataURL(f);
      return;
    }
  }
}

// ── 流转确认弹窗（替代原生 prompt）──
function openTransition(id,to,label){
  pendingTr={id:+id,to,label:label||(`流转 → ${to}`)};
  $('#trTitle').textContent='执行：'+pendingTr.label;
  $('#trInfo').innerHTML=`将以【<span>${role}·${esc(me())}</span>】身份执行「${esc(pendingTr.label)}」，操作将留痕（人员·角色·时间）。`;
  $('#trComment').value='';
  $('#trMask').classList.add('show');$('#trModal').classList.add('show');
  setTimeout(()=>$('#trComment').focus(),50);
}
function closeTr(){pendingTr=null;$('#trMask').classList.remove('show');$('#trModal').classList.remove('show');}
async function confirmTransition(){
  if(!pendingTr)return;
  const {id,to,label}=pendingTr;
  try{
    await api(`/api/bugs/${id}/transition`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to,by_role:role,actor:me(),comment:$('#trComment').value.trim()})});
    toast('✅ 已执行：'+label);closeTr();refreshAll();if(curBugId===id)openBug(id);
  }catch(e){toast('⚠️ '+e.message);}
}

async function addComment(id){
  const v=$('#eCmt').value.trim();if(!v){toast('评论不能为空');return;}
  try{await api(`/api/bugs/${id}/comment`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({by_role:role,actor:me(),comment:v})});$('#eCmt').value='';refreshAll();openBug(id);}
  catch(e){toast('⚠️ '+e.message);}
}
async function deleteBug(id){
  if(!confirm(`确定删除缺陷 #${id} 吗？该操作仅产品可执行，且会记入审计日志。`))return;
  try{await api(`/api/bugs/${id}?by_role=${encodeURIComponent(role)}&actor=${encodeURIComponent(me())}`,{method:'DELETE'});
    toast('已删除并写入审计日志');curBugId=null;closeDrawer();refreshAll();}
  catch(e){toast('⚠️ '+e.message);}
}

function refreshAll(){loadDash().catch(()=>{});loadFeed().catch(()=>{});if($('#tab-list').style.display!=='none')loadList().catch(()=>{});if($('#tab-mine').style.display!=='none')loadMine().catch(()=>{});}
function tick(){if(!live)return;$('#liveTxt').textContent='实时 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false});refreshAll();}

// ── 栏目切换（核心修复：非当前页 display:'none'）──
const TABS=['dash','list','mine','diagram','idea'];
function switchTab(tab){
  document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
  TABS.forEach(t=>$('#tab-'+t).style.display=t===tab?'grid':'none');
  if(tab==='dash'){loadDash().catch(()=>{});loadFeed().catch(()=>{});}
  if(tab==='list')loadList().catch(()=>{});
  if(tab==='mine')loadMine().catch(()=>{});
  if(tab==='idea')renderIdea();
}

// 新建缺陷
function fillNewSelects(){
  const pri=$('#nPri'),sev=$('#nSev');
  if(pri.options.length)return;
  (META?.priorities||['P0-紧急','P1-高','P2-中','P3-低']).forEach(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;pri.appendChild(o);});
  (META?.severities||['致命','严重','一般','轻微']).forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=s;sev.appendChild(o);});
}
function openNew(){$('#newRole').textContent=role;fillNewSelects();$('#newMask').classList.add('show');$('#newModal').classList.add('show');$('#nTitle').value='';$('#nDesc').value='';$('#nReporter').value=$('#who').value.trim();$('#nPri').selectedIndex=2;$('#nSev').selectedIndex=2;$('#nTitle').focus();}
function closeNew(){$('#newMask').classList.remove('show');$('#newModal').classList.remove('show');}
async function submitNew(){
  const title=$('#nTitle').value.trim();
  if(!title){toast('请填写标题');return;}
  try{
    const body={title,description:$('#nDesc').value.trim(),reporter:$('#nReporter').value.trim(),
      priority:$('#nPri').value,severity:$('#nSev').value,project:$('#nProj')?.value||META.default_project,
      by_role:role,actor:$('#nReporter').value.trim()||me()};
    const j=await api('/api/bugs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    toast('已创建 #'+j.id+' '+j.title);closeNew();refreshAll();
  }catch(e){toast('⚠️ '+e.message);}
}

// ── 绑定 ──
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
$('#role').onchange=()=>{role=$('#role').value;loadWho();refreshAll();
  // 身份变了，若详情抽屉开着，按新身份重新渲染按钮与权限
  if($('#drawer').classList.contains('show')&&curBugId)openBug(curBugId);};
$('#who').onchange=()=>{saveWho();if($('#tab-mine').style.display!=='none')loadMine();};
$('#refresh').onclick=()=>loadList();
$('#fStatus').onchange=loadList;$('#fPri').onchange=loadList;$('#fProj').onchange=loadList;$('#fAssignee').oninput=loadList;$('#fRisk').onchange=loadList;
let _kwT=null;$('#fSearch').oninput=()=>{clearTimeout(_kwT);_kwT=setTimeout(loadList,250);};
$('#btnExport').onclick=()=>{
  const st=$('#fStatus').value,kw=($('#fSearch').value||'').trim();
  let u=API+'/api/bugs/export?role='+encodeURIComponent(role);
  if(kw)u+='&q='+encodeURIComponent(kw);
  if(st)u+='&status='+encodeURIComponent(st);
  const a=document.createElement('a');a.href=u;a.click();toast('⬇ 已导出 CSV（按当前筛选）');
};
$('#dClose').onclick=closeDrawer;$('#mask').onclick=closeDrawer;
$('#btnNew').onclick=openNew;$('#newClose').onclick=closeNew;$('#newCancel').onclick=closeNew;$('#newMask').onclick=closeNew;$('#newSubmit').onclick=submitNew;
$('#trClose').onclick=closeTr;$('#trCancel').onclick=closeTr;$('#trMask').onclick=closeTr;$('#trConfirm').onclick=confirmTransition;
$('#trComment').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();confirmTransition();}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeDrawer();closeNew();closeTr();}});
function closeDrawer(){$('#mask').classList.remove('show');$('#drawer').classList.remove('show');curBugId=null;}
$('#toggleLive').onclick=()=>{live=!live;$('#liveDot').classList.toggle('off',!live);$('#liveTxt').textContent=live?'实时已开启':'已暂停';$('#toggleLive').textContent=live?'暂停':'继续';if(live)tick();};
$('#btnNotify').onclick=toggleNotify;
$('#btnLogout').onclick=logout;
document.addEventListener('paste',uploadPaste);

// ── 实时协作：WebSocket 推送（失败自动回退轮询）──
let wsOk=false;
function startTimer(){clearInterval(timer);if(wsOk)return;timer=setInterval(tick,(META?.config?.auto_refresh_sec||5)*1000);}
function connectWS(){
  try{
    const proto=location.protocol==='https:'?'wss':'ws';
    const tk=localStorage.getItem('defect_token')||'';
    const ws=new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(tk)}`);
    ws.onopen=()=>{wsOk=true;clearInterval(timer);};
    ws.onmessage=()=>{refreshAll();};
    ws.onclose=()=>{wsOk=false;startTimer();setTimeout(connectWS,3000);};
    ws.onerror=()=>{try{ws.close();}catch(_){}};
  }catch(_){startTimer();}
}
function logout(){localStorage.removeItem('defect_token');location.href='/';}

(async()=>{
  const tk=localStorage.getItem('defect_token');
  if(!tk){location.href='/';return;}
  let me0;
  try{me0=await api('/api/me');}catch(e){localStorage.removeItem('defect_token');location.href='/';return;}
  role=me0.role;
  $('#who').value=me0.display_name;
  $('#role').value=me0.role;
  $('#userChip').textContent=`${me0.display_name} · ${me0.role} · ${me0.workspace_name}`;
  loadWho();updateNotifyBtn();await loadMeta();await refreshAll();tick();startTimer();connectWS();
})();
