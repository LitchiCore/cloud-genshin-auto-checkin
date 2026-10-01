from pathlib import Path, PurePosixPath
import runpy
from tempfile import TemporaryDirectory
import unittest


SECURITY_CONFIG = '''server {
    listen [2001:db8::1]:8000 ssl;
    ssl_certificate /etc/letsencrypt/live/old/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/old/privkey.pem;
    access_log /var/log/nginx/cloud-genshin-direct-access.log ob_security;
    if ($ob_sensitive_path) { return 404; }
    location ~ ^/(?:\\.git|\\.env) { return 404; }
    location / {
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_pass http://127.0.0.1:8001;
    }
}
'''


class ConfigTests(unittest.TestCase):
    def test_certificate_patch_preserves_security_directives(self):
        helper = Path(__file__).resolve().parents[1] / 'deploy/helpers/cloud-genshin-ipv6-update'
        patch = runpy.run_path(str(helper))['patch_nginx_cert']
        with TemporaryDirectory() as directory:
            site = Path(directory) / 'site'
            site.write_text(SECURITY_CONFIG, encoding='utf-8')
            patch.__globals__['NGINX_SITE'] = site
            patch(PurePosixPath('/etc/letsencrypt/live/new'))
            actual = site.read_text(encoding='utf-8')
        for directive in (
            'access_log /var/log/nginx/cloud-genshin-direct-access.log ob_security;',
            'if ($ob_sensitive_path) { return 404; }',
            'location ~ ^/(?:\\.git|\\.env) { return 404; }',
            'proxy_set_header X-Real-IP $remote_addr;',
            'proxy_set_header X-Forwarded-For $remote_addr;',
            'proxy_pass http://127.0.0.1:8001;',
        ):
            self.assertIn(directive, actual)
        self.assertIn('listen [::]:8000 ssl;', actual)
        self.assertIn('/new/fullchain.pem;', actual)
        self.assertIn('/new/privkey.pem;', actual)


if __name__ == '__main__':
    unittest.main()
