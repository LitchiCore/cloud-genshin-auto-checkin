import {api,session,refreshSession} from './api.js';
import {e,icon,badge,date,nextRun,resultLabel,toast,busy,field,empty,modal,dialogHead,copy} from './ui.js';

const heading=(eyebrow,title,description='',action='')=>`<header class="page-head"><div><p class="eyebrow">${e(eyebrow)}</p><h1>${e(title)}</h1>${description?`<p class="description">${e(description)}</p>`:''}</div>${action}</header>`;
const accountURL=name=>'/accounts/'+encodeURIComponent(name);
const loginNeeded=a=>['unbound','login_expired'].includes(a.status);
const loginLabel=a=>a.status==='unbound'?'绑定云原神':'重新登录';
const loginButton=a=>`<button class="button primary" data-login="${e(a.username)}">${icon('key')}${loginLabel(a)}</button>`;

function recordTable(records) {
  if(!records.length)return empty('还没有签到记录','绑定云原神后，开启每日签到即可自动积累记录。');
  return `<div class="table-wrap"><table><thead><tr><th>运行时间</th><th>结果</th><th>时长变化</th><th class="duration">耗时</th></tr></thead><tbody>${records.map(r=>`<tr><td>${e(date(r.time))}</td><td><span class="record-result ${e(r.result)}">${icon(r.result==='ok'?'check':'alert')}${resultLabel(r.result)}</span></td><td>${r.minutes===null?'未记录':`${r.minutes>0?'+':''}${r.minutes} 分钟`}</td><td class="duration">${r.duration===null?'未记录':r.duration+' 秒'}</td></tr>`).join('')}</tbody></table></div>`;
}

function metrics(a) {
  return `<div class="metrics"><div><span class="metric-label">免费时长 · 上次记录</span><strong>${a.minutes===null||a.minutes===undefined?'尚无记录':e(a.minutes)+' <small>分钟</small>'}</strong></div><div><span class="metric-label">每日签到</span><strong class="metric-text">${!a.timer.known?'暂时无法读取':a.timer.enabled?'已开启':'已暂停'}</strong></div><div><span class="metric-label">${a.timer.enabled?'下一次签到':'签到安排'}</span><strong class="metric-text">${e(nextRun(a.timer))}</strong></div></div>`;
}

function accountCard(a,admin) {
  if(a.status==='unbound')return `<article class="account-card"><div class="account-card-head"><a class="account-name" href="${accountURL(a.username)}" data-nav><span class="avatar">${e(a.username.slice(0,2).toUpperCase())}</span><span>${e(a.username)}</span></a>${badge(a.status)}</div><div class="onboarding-copy"><h2>${admin?'等待用户绑定云原神':'绑定云原神，开始每日签到'}</h2><p>${admin?'用户接受邀请并完成扫码后，就可以管理自己的每日签到。':'用米游社或原神 App 扫码确认，即可开启自动签到。'}</p></div><footer class="card-footer"><p class="muted">${admin?'账号资料由用户自行绑定':'网站密码与云原神登录相互独立'}</p><div class="actions">${admin?`<a class="button secondary" href="${accountURL(a.username)}" data-nav>查看详情${icon('arrow')}</a>`:loginButton(a)}</div></footer></article>`;
  return `<article class="account-card"><div class="account-card-head"><a class="account-name" href="${accountURL(a.username)}" data-nav><span class="avatar">${e(a.username.slice(0,2).toUpperCase())}</span><span>${e(a.username)}</span></a>${badge(a.status)}</div>
  <p class="account-note">${a.checked_at?'检查于 '+e(date(a.checked_at)):'绑定后即可检查云原神登录状态'}</p>${metrics(a)}<footer class="card-footer"><p class="muted">${a.latest?'最近运行：'+e(date(a.latest.time))+' · '+resultLabel(a.latest.result):'还没有签到记录'}</p><div class="actions">${loginNeeded(a)?loginButton(a):''}<a class="button ${loginNeeded(a)?'secondary':'primary'}" href="${accountURL(a.username)}" data-nav>查看详情${icon('arrow')}</a></div></footer></article>`;
}

function attachLogin(root,navigate) {
  root.querySelectorAll('[data-login]').forEach(button=>button.addEventListener('click',async()=>{
    busy(button,true,'正在生成…');
    try {await api(`accounts/${encodeURIComponent(button.dataset.login)}/login`,{method:'POST'});navigate(accountURL(button.dataset.login)+'/login');}
    catch(error){toast(error.message,'error');busy(button,false);}
  }));
}

export async function home({user,navigate}) {
  const {accounts}=await api('accounts');
  const admin=user.role==='admin';
  const warning=accounts.filter(a=>a.status==='login_expired').length;
  return {title:admin?'账号概览':'我的签到',html:heading('CLOUD GENSHIN',admin?'账号概览':'我的云原神',admin?`${accounts.length} 个账号${warning?'，'+warning+' 个需要重新登录':'，在这里查看每日签到与登录状态。'}`:'每天的签到，交给这里。',admin?'<a class="button secondary" href="/manage" data-nav>'+icon('users')+'管理账号</a>':'')+
    `<section class="account-list">${accounts.length?accounts.map(a=>accountCard(a,admin)).join(''):empty('添加第一个账号','创建邀请，让用户绑定自己的云原神账号。')}</section>`+
    '<p class="page-footnote">登录状态与免费时长来自最近一次检查，并非实时在线状态。</p>',
    setup(root){attachLogin(root,navigate);}};
}

export async function account({username,user,navigate,reload}) {
  const a=await api('accounts/'+encodeURIComponent(username));
  const needs=loginNeeded(a);
  const summary=a.status==='unbound'?'先绑定你的云原神账号':a.status==='login_expired'?'重新登录后即可继续签到':a.checking?'正在检查登录并签到…':a.status==='error'?'最近一次检查未完成':'签到与登录状态';
  const explanation=a.status==='unbound'?'用米游社或原神 App 扫码，确认后即可开启每日签到。':a.status==='login_expired'?'云原神登录已过期。重新扫码不会更改你的网站密码和历史记录。':a.checking?'结果返回后，此页面会自动更新。':a.checked_at?'上次检查：'+date(a.checked_at):'你可以立即检查一次，确认当前登录状态。';
  const timerDisabled=!a.timer.known||a.checking;
  const html=`<a class="back-link" href="/" data-nav>${icon('back')}返回${user.role==='admin'?'账号概览':'我的签到'}</a>`+heading('ACCOUNT',username,'云原神账号',!needs?`<details class="more"><summary class="button secondary">更多操作</summary><div class="more-menu"><button data-login="${e(username)}">${icon('refresh')}验证或更新云原神登录</button></div></details>`:'')+
    `<section class="status-card ${needs?'attention':''}"><div class="status-icon">${icon(needs?'key':'cloud')}</div><div class="status-copy">${badge(a.status)}<h2>${e(summary)}</h2><p>${e(explanation)}</p></div>${needs?loginButton(a):''}</section>${metrics(a)}
    <section class="panel"><div class="section-head"><div><h2>每日签到</h2><p>开启后每天自动访问云原神，领取免费时长。</p></div><label class="switch"><input id="task-toggle" type="checkbox" ${a.timer.enabled?'checked':''} ${timerDisabled||a.status==='unbound'?'disabled':''} aria-label="每日签到"><span></span></label></div>
    <div class="task-row"><span>${icon('clock')}${e(nextRun(a.timer))}</span><button class="button secondary" id="run-task" ${a.checking||needs?'disabled':''}>${a.checking?'<span class="spinner"></span>检查中…':icon('play')+'立即检查并签到'}</button></div>
    ${a.auto_paused?'<p class="inline-note">由于连续登录失效，签到已自动暂停。重新登录成功后会尝试恢复。</p>':''}</section>
    <section class="panel records"><div class="section-head"><div><h2>最近运行</h2><p>最近 20 次检查与签到结果</p></div><button class="text-button" id="raw-logs">完整日志${icon('arrow')}</button></div>${recordTable(a.records)}</section>
    ${a.screenshot?`<details class="panel screenshot-panel"><summary>${icon('cloud')}查看上次运行截图</summary><p class="muted">用于查看云原神页面状态。</p><a href="/api/accounts/${e(username)}/screenshot" target="_blank" rel="noopener"><img src="/api/accounts/${e(username)}/screenshot?v=${encodeURIComponent(a.checked_at||'')}" alt="上次云原神运行截图" loading="lazy"></a></details>`:''}`;
  let poll;
  return {title:username+' · 账号详情',html,setup(root){
    attachLogin(root,navigate);
    root.querySelector('#task-toggle').addEventListener('change',async evt=>{
      const input=evt.target;input.disabled=true;const enabled=input.checked;
      try {await api(`accounts/${encodeURIComponent(username)}/task`,{method:'POST',data:{action:enabled?'enable':'disable'}});toast(enabled?'每日签到已开启':'每日签到已暂停');reload();}
      catch(error){input.checked=!enabled;toast(error.message,'error');input.disabled=false;}
    });
    root.querySelector('#run-task').addEventListener('click',async evt=>{
      const button=evt.currentTarget;busy(button,true,'已提交…');
      try {await api(`accounts/${encodeURIComponent(username)}/task`,{method:'POST',data:{action:'run'}});toast('已开始检查，结果会自动更新');reload();}
      catch(error){toast(error.message,'error');busy(button,false);}
    });
    root.querySelector('#raw-logs').addEventListener('click',async evt=>{
      const button=evt.currentTarget;busy(button,true,'加载中…');
      try {const {lines}=await api(`accounts/${encodeURIComponent(username)}/logs`);modal(dialogHead('运行日志')+`<p class="muted">最近 500 行 · ${e(username)}</p><pre class="log-view">${e(lines.join('\n')||'暂无运行日志')}</pre>`);}
      catch(error){toast(error.message,'error');}finally{busy(button,false);}
    });
    if(a.checking)poll=setInterval(async()=>{try{const next=await api('accounts/'+encodeURIComponent(username));if(!next.checking)reload();}catch{}},3000);
  },destroy(){clearInterval(poll);}};
}

export async function loginCloud({username,navigate,reload}) {
  const a=await api('accounts/'+encodeURIComponent(username));
  const job=a.job;const active=job.running;
  const success=job.phase==='success'&&a.status==='ok';
  const labels={preparing:'正在生成二维码',waiting_scan:'请扫码，并在手机上确认',verifying:'正在验证云原神登录',failed:'本次登录未完成',expired:'重新登录云原神',idle:'绑定或更新云原神登录'};
  const title=success?'云原神登录已恢复':labels[job.phase]||'绑定云原神';
  const description=success?'登录验证完成，返回账号详情即可查看签到安排。':job.message||(active?'正在连接云原神，请稍候。':'生成二维码后，使用米游社或原神 App 扫码。');
  const step=success?3:job.phase==='verifying'?3:job.phase==='waiting_scan'?2:1;
  const qrURL=`/api/accounts/${encodeURIComponent(username)}/login/qr?job=${encodeURIComponent(job.job_id)}`;
  let poll,countdown,remaining=job.remaining;
  return {title:username+' · 云原神登录',html:`<a class="back-link" href="${accountURL(username)}" data-nav>${icon('back')}返回账号详情</a>`+heading(username,'云原神登录','扫码只更新云原神登录状态，网站密码保持不变。')+
    `<div class="login-flow">${['生成二维码','手机扫码确认','验证登录'].map((label,i)=>`<div class="flow-step ${i+1===step?'active':''} ${i+1<step?'done':''}"><span>${i+1<step?icon('check'):i+1}</span><strong>${label}</strong></div>`).join('')}</div>
    <section class="scan-panel"><div class="scan-content" role="status"><div class="scan-symbol ${success?'success':''}">${icon(success?'check':job.phase==='failed'?'alert':'key')}</div><h2>${e(title)}</h2><p>${e(description)}</p>
    ${job.qr_ready?`<div class="qr-frame"><img src="${qrURL}" alt="云原神登录二维码" width="224" height="224"></div><p class="qr-countdown">有效期剩余 <strong id="countdown">${remaining}</strong> 秒</p><a class="text-button" href="${qrURL}&download=1">下载二维码</a><p class="small">同一手机操作时，可以下载图片后在 App 中识别。</p>`:active?'<div class="scan-loading"><span class="spinner"></span>请稍候</div>':''}
    <div class="scan-actions">${!active&&!success?`<button class="button primary" data-login="${e(username)}">${icon('refresh')}${job.phase==='idle'||job.phase==='expired'?'生成二维码':'重新生成二维码'}</button>`:''}${success?`<a class="button primary" href="${accountURL(username)}" data-nav>返回账号详情${icon('arrow')}</a>`:''}</div></div></section>
    <p class="page-footnote">请确认扫码的是你要绑定的云原神账号。登录成功后，会保留原有的签到安排。</p>`,
    setup(root){attachLogin(root,navigate);if(active){poll=setInterval(async()=>{try{const next=await api(`accounts/${encodeURIComponent(username)}/login`);if(next.phase!==job.phase||next.qr_ready!==job.qr_ready||next.job_id!==job.job_id){reload();return;}remaining=next.remaining;}catch{}},2000);
    countdown=setInterval(()=>{const node=root.querySelector('#countdown');if(node&&remaining!==null){remaining=Math.max(0,remaining-1);node.textContent=remaining;if(!remaining){clearInterval(countdown);reload();}}},1000);}},destroy(){clearInterval(poll);clearInterval(countdown);}};
}

export async function signin({navigate}) {
  return {title:'登录网站',public:true,html:`<div class="auth-intro"><span class="auth-mark">${icon('cloud')}</span><h1>每天签到，从容一点。</h1><p>登录网站，查看并管理你的云原神签到。</p></div><section class="auth-card"><h2>欢迎回来</h2><p class="muted">使用你的网站账号登录</p><form id="signin-form">${field('账号名','username',{autocomplete:'username',placeholder:'输入账号名'})}${field('网站密码','password',{type:'password',autocomplete:'current-password',placeholder:'输入网站密码'})}<p class="form-error" role="alert" id="form-error" hidden></p><button class="button primary full" type="submit">登录网站${icon('arrow')}</button></form><p class="auth-switch">收到邀请？<a href="/register" data-nav>创建网站账号</a></p></section>`,setup(root){
    root.querySelector('#signin-form').addEventListener('submit',async evt=>{evt.preventDefault();const form=evt.currentTarget,button=form.querySelector('button');busy(button,true,'正在登录…');const errorNode=root.querySelector('#form-error');errorNode.hidden=true;
      try {await api('login',{method:'POST',data:Object.fromEntries(new FormData(form))});await refreshSession();let next=new URLSearchParams(location.search).get('next')||'/';if(!/^\/(?:accounts\/[A-Za-z0-9_-]{1,64}(?:\/login)?|manage|network|profile)?$/.test(next))next='/';navigate(next,true);}
      catch(error){errorNode.textContent=error.message;errorNode.hidden=false;busy(button,false);if(error.retry){button.disabled=true;let left=error.retry;const timer=setInterval(()=>{left--;button.textContent=`${left} 秒后重试`;if(left<=0){clearInterval(timer);busy(button,false);}},1000);root._cooldown=timer;}}
    });root.querySelector('input').focus();},destroy(){clearInterval(document.querySelector('#page')?._cooldown);}};
}

export async function register({navigate}) {
  if(session.user)return {title:'接受邀请',public:true,html:`<section class="auth-card"><h2>当前已登录 ${e(session.user.username)}</h2><p class="muted">使用邀请创建另一个账号前，请先退出当前网站账号。</p><button class="button primary full" id="switch-account">退出并接受邀请</button><p class="auth-switch"><a href="/" data-nav>继续使用当前账号</a></p></section>`,setup(root){root.querySelector('#switch-account').addEventListener('click',async evt=>{busy(evt.currentTarget,true,'正在退出…');try{await api('logout',{method:'POST'});await refreshSession();navigate(location.pathname+location.search,true);}catch(error){toast(error.message,'error');busy(evt.currentTarget,false);}});}};
  const code=new URLSearchParams(location.search).get('invite')||'';
  let username=null,error='';
  if(code){try{username=(await api('invites/resolve',{method:'POST',data:{invite:code}})).username;}catch(err){error=err.message;}}
  return {title:'创建网站账号',public:true,html:`<div class="auth-intro"><span class="auth-mark">${icon('cloud')}</span><h1>你的每日签到小助手。</h1><p>设置网站密码后，就可以绑定自己的云原神账号。</p></div><section class="auth-card"><h2>创建网站账号</h2><p class="muted" id="invite-account">${username?'邀请账号：'+e(username):'使用管理员发给你的邀请码'}</p><form id="register-form">${field('邀请码','invite',{value:code,placeholder:'XXXXX-XXXXX',autocomplete:'off'})}${field('设置网站密码','password',{type:'password',minlength:8,autocomplete:'new-password',placeholder:'至少 8 位'})}${field('确认网站密码','confirm',{type:'password',minlength:8,autocomplete:'new-password',placeholder:'再次输入密码'})}<p class="form-error" role="alert" id="form-error" ${error?'':'hidden'}>${e(error)}</p><button class="button primary full" type="submit">创建账号${icon('arrow')}</button></form><p class="auth-switch">已有网站账号？<a href="/signin" data-nav>去登录</a></p></section>`,setup(root){root.querySelector('#register-form').addEventListener('submit',async evt=>{evt.preventDefault();const form=evt.currentTarget,button=form.querySelector('button'),data=Object.fromEntries(new FormData(form)),err=root.querySelector('#form-error');err.hidden=true;busy(button,true,'正在创建…');
      try {if(data.password!==data.confirm)throw new Error('两次输入的密码不一致。');await api('register',{method:'POST',data});await refreshSession();toast('网站账号已创建，接下来绑定云原神');navigate('/',true);}
      catch(error){err.textContent=error.message;err.hidden=false;busy(button,false);}
    });}};
}

function inviteDialog(username,invite,publicURL) {
  const link=(publicURL||location.origin)+'/register?invite='+encodeURIComponent(invite.code);
  const dialog=modal(dialogHead('邀请 '+username)+`<p class="muted">把邀请链接发给对方，由对方设置网站密码并绑定云原神。</p><label class="field"><span>邀请链接</span><input readonly value="${e(link)}" aria-label="邀请链接"></label><div class="invite-code"><span>邀请码</span><strong>${e(invite.code)}</strong></div><p class="small">有效至 ${e(date(invite.expires))} · 只能使用一次</p><div class="dialog-actions"><button class="button secondary" id="copy-code">复制邀请码</button><button class="button primary" id="copy-link">${icon('link')}复制邀请链接</button></div>`);
  dialog.querySelector('#copy-code').addEventListener('click',()=>copy(invite.code));dialog.querySelector('#copy-link').addEventListener('click',()=>copy(link));
}

export async function manage({user,reload,navigate}) {
  if(user.role!=='admin')return forbidden();
  const {users,public_url}=await api('users');
  return {title:'账号管理',html:heading('ADMINISTRATION','账号管理','发送邀请，让用户自己设置网站密码、绑定云原神。',`<button class="button primary" id="add-user">${icon('plus')}添加账号</button>`)+
    `<section class="panel user-list">${users.map(u=>`<article class="user-row"><div class="user-identity"><span class="avatar">${e(u.username.slice(0,2).toUpperCase())}</span><div><h3>${e(u.username)}${u.role==='admin'?'<span class="role-tag">管理员</span>':''}</h3><p>${u.registered?'网站账号已注册':'等待接受邀请'}${u.protected?' · 受保护账号':''}</p></div></div><div class="actions"><a class="button secondary" href="${accountURL(u.username)}" data-nav>账号详情</a>${!u.registered?`<button class="button primary" data-invite="${e(u.username)}">${icon('link')}邀请用户</button>`:''}${!u.protected&&u.username!==user.username?`<button class="icon-button danger-text" data-delete="${e(u.username)}" aria-label="删除 ${e(u.username)}">${icon('close')}</button>`:''}</div></article>`).join('')}</section><p class="page-footnote">账号本人可以管理自己的登录与签到。网络维护和账号删除仅供管理员使用。</p>`,setup(root){
    root.querySelector('#add-user').addEventListener('click',()=>{const dialog=modal(dialogHead('添加账号')+`<p class="muted">创建一个邀请，让对方自行完成网站注册和云原神绑定。</p><form id="create-user">${field('账号名','username',{placeholder:'字母、数字、下划线或短横线',autocomplete:'off'})}<p class="form-error" role="alert" hidden></p><div class="dialog-actions"><button class="button primary" type="submit">创建并生成邀请</button></div></form>`);dialog.querySelector('input').focus();dialog.querySelector('form').addEventListener('submit',async evt=>{evt.preventDefault();const form=evt.currentTarget,button=form.querySelector('button');busy(button,true,'正在创建…');try{const result=await api('users',{method:'POST',data:Object.fromEntries(new FormData(form))});dialog.close();await reload();inviteDialog(result.username,result.invite,public_url);}catch(error){const node=form.querySelector('.form-error');node.textContent=error.message;node.hidden=false;busy(button,false);}});});
    root.querySelectorAll('[data-invite]').forEach(button=>button.addEventListener('click',async()=>{const username=button.dataset.invite,u=users.find(u=>u.username===username);if(u.invite){inviteDialog(username,u.invite,public_url);return;}busy(button,true,'正在生成…');try{const result=await api(`users/${encodeURIComponent(username)}/invite`,{method:'POST'});u.invite=result;inviteDialog(username,result,public_url);}catch(error){toast(error.message,'error');}finally{busy(button,false);}}));
    root.querySelectorAll('[data-delete]').forEach(button=>button.addEventListener('click',()=>{const username=button.dataset.delete;const dialog=modal(dialogHead('删除 '+username)+`<p>删除后，该用户将无法登录网站，云原神资料和运行记录会移入服务器备份。</p><form>${field('输入账号名确认删除','confirm',{placeholder:username,autocomplete:'off'})}<p class="form-error" hidden role="alert"></p><div class="dialog-actions"><button class="button danger" type="submit">确认删除账号</button></div></form>`);dialog.querySelector('form').addEventListener('submit',async evt=>{evt.preventDefault();const form=evt.currentTarget,b=form.querySelector('button');busy(b,true,'正在删除…');try{await api(`users/${encodeURIComponent(username)}/delete`,{method:'POST',data:Object.fromEntries(new FormData(form))});dialog.close();toast('账号已删除');reload();}catch(error){const node=form.querySelector('.form-error');node.textContent=error.message;node.hidden=false;busy(b,false);}});}));
  }};
}

export async function profile({user,navigate}) {
  return {title:'个人设置',html:heading('PERSONAL','个人设置','管理你的网站账号。')+`<div class="profile-grid"><section class="panel"><div class="section-head"><div><h2>网站账号</h2><p>用于访问签到网站，与云原神账号独立。</p></div>${icon('shield')}</div><dl class="definition-list"><div><dt>账号名</dt><dd>${e(user.username)}</dd></div><div><dt>身份</dt><dd>${user.role==='admin'?'管理员':'普通用户'}</dd></div></dl><button class="button secondary" id="logout">${icon('logout')}退出网站</button></section><section class="panel"><div class="section-head"><div><h2>修改网站密码</h2><p>修改后需要在各设备重新登录网站。</p></div></div><form id="password-form">${field('当前网站密码','current',{type:'password',autocomplete:'current-password'})}${field('新网站密码','password',{type:'password',minlength:8,autocomplete:'new-password',placeholder:'至少 8 位'})}${field('确认新密码','confirm',{type:'password',minlength:8,autocomplete:'new-password'})}<p class="form-error" hidden role="alert"></p><button class="button primary" type="submit">保存新密码</button></form></section></div>`,setup(root){root.querySelector('#logout').addEventListener('click',async evt=>{busy(evt.currentTarget,true,'正在退出…');try{await api('logout',{method:'POST'});await refreshSession();navigate('/signin',true);}catch(error){toast(error.message,'error');busy(evt.currentTarget,false);}});root.querySelector('#password-form').addEventListener('submit',async evt=>{evt.preventDefault();const form=evt.currentTarget,b=form.querySelector('button');busy(b,true,'正在保存…');try{await api('password',{method:'POST',data:Object.fromEntries(new FormData(form))});await refreshSession();toast('密码已修改，请重新登录');navigate('/signin',true);}catch(error){const node=form.querySelector('.form-error');node.textContent=error.message;node.hidden=false;busy(b,false);}});}};
}

export async function network({user,reload}) {
  if(user.role!=='admin')return forbidden();
  const n=await api('network'),v=n.ipv6,u=n.update;
  const current=v.current_ipv6||v.ipv6||v.current||'暂未检查';
  const updating=['queued','running','busy'].includes(u.status);
  let poll;
  return {title:'网络设置',html:heading('ADMINISTRATION','网络设置','维护网站入口；普通用户不需要关心这些设置。')+`<section class="panel"><div class="section-head"><div><h2>主入口</h2><p>使用固定域名访问网站</p></div><span class="badge ok"><span class="dot"></span>Cloudflare Tunnel</span></div><p class="url-value">${e(n.public_url||location.origin)}</p></section><section class="panel"><div class="section-head"><div><h2>IPv6 直连</h2><p>网络地址变化后，可更新直连证书与入口。</p></div><span class="badge ${v.status==='ok'?'ok':'unknown'}"><span class="dot"></span>${v.status==='ok'?'最近检查正常':'需要检查'}</span></div><dl class="definition-list"><div><dt>当前地址</dt><dd class="mono">${e(current)}</dd></div><div><dt>最近检查</dt><dd>${e(date(v.checked_at||v.updated_at))}</dd></div></dl><p class="inline-note">${e(updating?u.message||'后台正在更新，请稍候。':u.status==='error'?u.message||'最近一次更新未完成。':'更新在后台执行，主域名入口保持可用。')}</p><button class="button secondary" id="network-update" ${updating?'disabled':''}>${updating?'<span class="spinner"></span>正在更新…':icon('refresh')+'更新直连入口'}</button></section>`,setup(root){root.querySelector('#network-update').addEventListener('click',async evt=>{busy(evt.currentTarget,true,'正在启动…');try{await api('network',{method:'POST'});reload();}catch(error){toast(error.message,'error');busy(evt.currentTarget,false);}});if(updating)poll=setInterval(async()=>{try{const next=await api('network');if(!['queued','running','busy'].includes(next.update.status))reload();}catch{}},3000);},destroy(){clearInterval(poll);}};
}

export function forbidden() {return {title:'无法访问',html:empty('你没有权限访问这个页面','返回首页，查看你自己的签到账号。')+'<a class="button primary" href="/" data-nav>返回首页</a>'};}
