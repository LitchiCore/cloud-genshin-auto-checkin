export const session = {user:null,csrf:null};

export class ApiError extends Error {
  constructor(message,status,retry=0) {super(message);this.status=status;this.retry=retry;}
}

export async function api(path,{method='GET',data,signal}={}) {
  const prefix = method==='POST' && /^(accounts\/|users|network)/.test(path) ? '/admin/api/' : '/api/';
  const response = await fetch(prefix+path,{method,signal,credentials:'same-origin',cache:'no-store',
    headers:method==='POST'?{'Content-Type':'application/json','X-CSRF-Token':session.csrf||''}:{},
    body:method==='POST'?JSON.stringify(data||{}):undefined});
  let result;
  try {result=await response.json();} catch {throw new ApiError('服务暂时无法连接，请稍后再试。',response.status);}
  if(!response.ok) {
    if(response.status===401) window.dispatchEvent(new CustomEvent('session-expired'));
    throw new ApiError(result.error||'操作未完成，请稍后重试。',response.status,result.retry_after);
  }
  return result;
}

export async function refreshSession() {
  Object.assign(session,await api('session'));
  return session.user;
}
