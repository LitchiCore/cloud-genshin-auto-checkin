from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import json
import secrets
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'web'))
import app
import store
import services
from login_guard import LoginGuard


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.patches=[patch.object(store,'DB',self.base/'web/users.db'),patch.object(services,'BASE',self.base),
            patch.object(services,'ACCOUNTS',self.base/'accounts'),patch.object(services,'LOGS',self.base/'logs'),
            patch.object(services,'timer_status',return_value={'enabled':False,'active':False,'known':True,'next':''}),
            patch.object(app,'LOGIN_GUARD',LoginGuard(user_limit=2,ip_limit=3))]
        for p in self.patches:p.start()
        store.init()
        self.password='test-password-'+secrets.token_hex(4)
        salt,digest=store.password_hash(self.password)
        self.cookies={}
        for name,role in [('lihe','admin'),('alice','user'),('bob','user')]:
            (services.ACCOUNTS/name/'screenshots').mkdir(parents=True)
            with store.database() as db:
                db.execute('INSERT INTO users(username,role,created_at,password_set,password_salt,password_hash) VALUES(?,?,?,1,?,?)',
                    (name,role,int(time.time()),salt,digest))
            self.cookies[name]=store.new_session(name).split(';')[0]
        (self.base/'web/site_config.json').write_text(json.dumps({'protected_accounts':['lihe']}))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.port=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def request(self,path,user=None,data=None,csrf=True,ip='192.0.2.1',raw=None,content_type='application/json'):
        connection=HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={'X-Real-IP':ip}
        if user:
            headers['Cookie']=self.cookies[user]
            if csrf:headers['X-CSRF-Token']=store.csrf_token(headers)
        body=json.dumps(data) if data is not None else raw
        if body is not None:headers['Content-Type']=content_type
        connection.request('POST' if body is not None else 'GET',path,body,headers)
        response=connection.getresponse();payload=response.read();status=response.status;h=dict(response.getheaders());connection.close()
        result=json.loads(payload) if payload and h.get('Content-Type','').startswith('application/json') else payload.decode()
        return status,h,result

    def test_owner_cannot_read_or_mutate_other_account_or_admin_data(self):
        self.assertEqual(self.request('/api/accounts')[0],401)
        result=self.request('/api/accounts','alice')[2]
        self.assertEqual([a['username'] for a in result['accounts']],['alice'])
        for tail in ('','/login','/logs','/screenshot','/login/qr'):
            self.assertEqual(self.request('/api/accounts/bob'+tail,'alice')[0],403)
        for tail in ('login','task'):
            self.assertEqual(self.request('/admin/api/accounts/bob/'+tail,'alice',{})[0],403)
        for path in ('/api/users','/api/network'):
            self.assertEqual(self.request(path,'alice')[0],403)
        self.assertEqual(self.request('/admin/api/users','alice',{'username':'mallory'})[0],403)
        self.assertEqual(self.request('/admin/api/users/bob/delete','alice',{'confirm':'bob'})[0],403)

    def test_mutations_require_csrf_bound_to_current_session(self):
        self.assertEqual(self.request('/admin/api/accounts/alice/login','alice',{},csrf=False)[0],403)
        with patch.object(services,'start_login',return_value={'phase':'preparing'}) as start:
            self.assertEqual(self.request('/admin/api/accounts/alice/login','alice',{})[0],202)
            start.assert_called_once_with('alice')
        self.assertNotEqual(store.csrf_token({'Cookie':self.cookies['alice']}),store.csrf_token({'Cookie':self.cookies['bob']}))

    def test_login_compatible_passwords_and_failure_limits(self):
        status,headers,_=self.request('/api/login',data={'username':'alice','password':self.password})
        self.assertEqual(status,200);self.assertIn('HttpOnly; Secure; SameSite=Lax',headers['Set-Cookie'])
        self.assertEqual(self.request('/api/login',data={'username':'alice','password':'wrong'},ip='192.0.2.2')[0],400)
        status,headers,_=self.request('/api/login',data={'username':'alice','password':'wrong'},ip='192.0.2.3')
        self.assertEqual(status,429);self.assertEqual(headers['Retry-After'],'300')
        self.assertEqual(self.request('/api/login',data={'username':'alice','password':self.password},ip='192.0.2.4')[0],429)

    def test_public_login_alias_uses_same_limits_and_helpers_use_admin_service(self):
        with patch.object(self.server,'server_port',8003):
            status,headers,_=self.request('/admin/api/login',data={'username':'alice','password':'wrong'})
            self.assertEqual(status,307);self.assertEqual(headers['Location'],'/api/login')
        with patch.object(self.server,'server_port',8001):
            status,headers,_=self.request('/api/accounts/alice/login','alice',{})
            self.assertEqual(status,307);self.assertEqual(headers['Location'],'/admin/api/accounts/alice/login')

    def test_invite_register_logs_in_user_and_invite_is_single_use(self):
        status,_,result=self.request('/admin/api/users','lihe',{'username':'new_user'})
        self.assertEqual(status,201)
        code=result['invite']['code']
        self.assertEqual(self.request('/api/invites/resolve',data={'invite':code})[2]['username'],'new_user')
        status,headers,_=self.request('/api/register',data={'invite':code,'password':self.password,'confirm':self.password})
        self.assertEqual(status,200);self.assertIn('Set-Cookie',headers)
        self.assertEqual(self.request('/api/register',data={'invite':code,'password':self.password,'confirm':self.password})[0],400)
        self.cookies['new_user']=headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('/api/session','new_user')[2]['user']['username'],'new_user')
        self.assertEqual(self.request('/api/accounts/new_user','new_user')[2]['status'],'unbound')

    def test_password_change_invalidates_all_sessions(self):
        new='changed-password-123'
        self.assertEqual(self.request('/api/password','alice',{'current':'wrong','password':new,'confirm':new})[0],400)
        self.assertEqual(self.request('/api/password','alice',{'current':self.password,'password':new,'confirm':new})[0],200)
        self.assertIsNone(self.request('/api/session','alice')[2]['user'])
        self.assertEqual(self.request('/api/login',data={'username':'alice','password':new})[0],200)

    def test_protected_deletion_and_unbound_task_cannot_run(self):
        self.assertEqual(self.request('/admin/api/users/lihe/delete','lihe',{'confirm':'lihe'})[0],409)
        self.assertEqual(self.request('/admin/api/accounts/alice/task','alice',{'action':'run'})[0],409)
        self.assertEqual(self.request('/admin/api/users','lihe',{'username':'../bad'})[0],400)
        self.assertEqual(self.request('/admin/api/users','lihe',{'username':'alice'})[0],409)

    def test_task_runs_as_owner_and_pending_state_finishes_on_health_update(self):
        directory=services.ACCOUNTS/'alice'
        (directory/'state.json').write_text(json.dumps({'minutes':100}))
        services.write_json(directory/'health.json',{'status':'ok','updated_at':'before'})
        with patch.object(services.subprocess,'run') as command:
            command.return_value.returncode=0
            self.assertEqual(self.request('/admin/api/accounts/alice/task','alice',{'action':'run'})[0],200)
            self.assertTrue(services.account('alice')['checking'])
            self.assertEqual(command.call_args.args[0][-2:],['run','alice'])
        services.write_json(directory/'health.json',{'status':'ok','updated_at':'after'})
        self.assertFalse(services.account('alice')['checking'])

    def test_invalid_request_and_static_traversal_and_security_headers(self):
        self.assertEqual(self.request('/api/login',raw='not-json')[0],400)
        self.assertEqual(self.request('/api/login',raw='username=x',content_type='application/x-www-form-urlencoded')[0],415)
        self.assertEqual(self.request('/assets/../store.py')[0],404)
        self.assertEqual(self.request('/api/login',raw='x'*17000)[0],413)
        status,headers,body=self.request('/')
        self.assertEqual(status,200);self.assertIn('app.js',body)
        self.assertEqual(self.request('/login')[0],200)
        self.assertEqual(headers['X-Frame-Options'],'DENY');self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertNotIn('unsafe-inline',headers['Content-Security-Policy'])


if __name__=='__main__':unittest.main()
