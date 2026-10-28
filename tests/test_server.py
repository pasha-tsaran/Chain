import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server
import storage


class HTTPTests(unittest.TestCase):
    def setUp(self):
        temp_root=storage.ROOT/'data'/'test-runs';temp_root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=temp_root)
        self.patch=patch.object(storage,'DATA',Path(self.temp.name));self.patch.start()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();self.patch.stop();self.temp.cleanup()

    def request(self,path,body=None,headers=None):
        data=None if body is None else json.dumps(body).encode()
        req=urllib.request.Request(self.url+path,data=data,headers=headers or {})
        try:
            return urllib.request.urlopen(req,timeout=3)
        except urllib.error.HTTPError as exc:
            return exc

    def test_public_settings_exclude_keys(self):
        storage.save_settings({**storage.DEFAULTS,'zerogpt_token':'testsecret','zerogpt_key':'anothersecret'})
        with patch('server.request_json',return_value={'models':[]}):
            with self.request('/api/status') as response:
                value=response.read().decode();self.assertEqual(response.status,200)
        self.assertNotIn('testsecret',value);self.assertNotIn('anothersecret',value)

    def test_cross_origin_and_dns_rebinding_blocked(self):
        with self.request('/api/articles',{}, {'X-Studio-Request':'1','Origin':'https://evil.example'}) as response:
            self.assertEqual(response.code,403)
        with self.request('/api/articles',headers={'Host':'evil.example'}) as response:
            self.assertEqual(response.code,403)

    def test_missing_csrf_header_rejected(self):
        with self.request('/api/settings',{}) as response:self.assertEqual(response.code,403)

    def test_settings_saved_without_exposing_key(self):
        body={'model':'llama3.2:3b','zerogpt_key':'sensitive'}
        with self.request('/api/settings',body,{'X-Studio-Request':'1'}) as response:
            self.assertEqual(response.status,200);self.assertNotIn('sensitive',response.read().decode())
        self.assertEqual(storage.settings()['model'],'llama3.2:3b')

    def test_traversal_unavailable(self):
        for path in ['/data/settings.json','/media/../settings.json','/../storage.py']:
            with self.request(path) as response:self.assertEqual(response.code,404)


if __name__=='__main__':unittest.main()
