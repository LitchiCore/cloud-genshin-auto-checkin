import {session,refreshSession} from './api.js';
import {e,icon,toast} from './ui.js';
import * as pages from './pages.js';

let cleanup=()=>{},revision=0;
const app=document.querySelector('#app');
if(location.pathname==='/login')history.replaceState({},'','/signin'+location.search);

function frame(html,publicPage=false) {
  if(publicPage)return `<main class="auth-layout"><a class="brand auth-brand" href="/" data-nav><span class="brand-mark">${icon('cloud')}</span><span>云原神<span class="brand-sub">每日签到</span></span></a><div id="page" class="auth-page">${html}</div><footer class="auth-footer">非官方签到工具 · 请妥善保管你的登录资料</footer></main>`;
  const user=session.user,admin=user.role==='admin',path=location.pathname;
  const nav=(url,name,symbol,selected)=>`<a href="${url}" class="nav-link ${selected?'active':''}" data-nav>${icon(symbol)}<span>${name}</span></a>`;
  return `<div class="app-layout"><aside class="sidebar"><a class="brand" href="/" data-nav><span class="brand-mark">${icon('cloud')}</span><span>云原神<span class="brand-sub">每日签到</span></span></a><nav aria-label="主导航">${nav('/',admin?'账号概览':'我的签到','home',path==='/'||path.startsWith('/accounts/'))}${admin?`<div class="nav-label">管理</div>${nav('/manage','账号管理','users',path==='/manage')}${nav('/network','网络设置','settings',path==='/network')}`:''}</nav><div class="sidebar-bottom">${nav('/profile','个人设置','key',path==='/profile')}<div class="identity"><span class="avatar small-avatar">${e(user.username.slice(0,2).toUpperCase())}</span><span><strong>${e(user.username)}</strong><small>${admin?'管理员':'普通用户'}</small></span></div></div></aside><div class="mobile-bar"><a class="brand" href="/" data-nav><span class="brand-mark">${icon('cloud')}</span><span>云原神签到</span></a><a class="mobile-profile" href="/profile" data-nav aria-label="个人设置">${icon('key')}</a></div><main id="page" class="main" tabindex="-1">${html}</main>${admin?`<nav class="mobile-nav" aria-label="移动导航">${nav('/','概览','home',path==='/'||path.startsWith('/accounts/'))}${nav('/manage','账号','users',path==='/manage')}${nav('/network','网络','settings',path==='/network')}</nav>`:''}</div>`;
}

export async function navigate(path,replace=false) {
  const url=new URL(path,location.origin);if(url.origin!==location.origin)return;
  history[replace?'replaceState':'pushState']({},'',url.pathname+url.search);
  window.scrollTo({top:0,behavior:'instant'});
  await render();
}

async function render() {
  const id=++revision;cleanup();cleanup=()=>{};
  const path=location.pathname,publicPage=['/signin','/register','/reset'].includes(path);
  if(!session.user&&!publicPage){await navigate('/signin?next='+encodeURIComponent(path),true);return;}
  if(session.user&&path==='/signin'){await navigate('/',true);return;}
  if(!publicPage)app.innerHTML=frame('<div class="loading"><span class="spinner"></span>正在加载…</div>');
  const ctx={user:session.user,navigate,reload:render};
  let page;
  try {
    if(path==='/')page=await pages.home(ctx);
    else if(path==='/signin')page=await pages.signin(ctx);
    else if(path==='/register')page=await pages.register(ctx);
    else if(path==='/reset')page=await pages.resetPassword(ctx);
    else if(path==='/profile')page=await pages.profile(ctx);
    else if(path==='/manage')page=await pages.manage(ctx);
    else if(path==='/network')page=await pages.network(ctx);
    else {const m=path.match(/^\/accounts\/([A-Za-z0-9_-]{1,64})(\/login)?$/);page=m?await(m[2]?pages.loginCloud:pages.account)({...ctx,username:m[1]}):pages.forbidden();}
  }catch(error){if(id!==revision)return;if(error.status===401)return;page={title:'暂时无法打开',html:`<div class="error-page">${icon('alert')}<h1>${error.status===403?'无法访问这个账号':error.status===404?'账号不存在':'暂时无法加载'}</h1><p>${e(error.message)}</p><div class="actions"><button class="button primary" id="retry-page">重试</button><a class="button secondary" href="/" data-nav>返回首页</a></div></div>`,setup(root){root.querySelector('#retry-page').addEventListener('click',render);}};}
  if(id!==revision)return;
  document.title=page.title+' · 云原神签到';app.innerHTML=frame(page.html,page.public||publicPage);
  const root=document.querySelector('#page');page.setup?.(root);cleanup=()=>page.destroy?.();
}

document.addEventListener('click',evt=>{const link=evt.target.closest('a[data-nav]');if(!link||evt.button!==0||evt.ctrlKey||evt.metaKey||evt.shiftKey||evt.altKey)return;evt.preventDefault();navigate(link.getAttribute('href'));});
window.addEventListener('popstate',render);
window.addEventListener('session-expired',()=>{session.user=null;session.csrf=null;toast('网站登录已过期，请重新登录','error');navigate('/signin?next='+encodeURIComponent(location.pathname),true);});
try {await refreshSession();await render();}catch(error){app.innerHTML=`<main class="error-page"><h1>暂时无法连接</h1><p>${e(error.message)}</p><a class="button primary" href="/">重新打开网站</a></main>`;}
