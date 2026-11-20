"""Additional offline regression tests for the independent local dashboard.

No browser, GPU, real ComfyUI, cloud or external dependencies required.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import urllib.parse

from assetfoundry.dashboard import DashboardController
from assetfoundry.models import Settings
from assetfoundry.webapp import DashboardServer
from assetfoundry.state import JobStore

# Valid signature is sufficient for a scan. The preview test uses Pillow if available.
PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489')


def graph():
    return {'1': {'class_type': 'LoadImage', 'inputs': {'image': 'x.png'}},
            '2': {'class_type': 'MakeMesh', 'inputs': {'image': ['1',0]}},
            '3': {'class_type': 'SaveGLB', 'inputs': {'mesh': ['2',0], 'filename_prefix': 'x'}}}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'user-state')}).start()
        (self.root / 'Tree').mkdir()
        (self.root / 'Tree' / 'Baum.png').write_bytes(PNG)
        (self.root / 'Stein.png').write_bytes(PNG)
        self.workflow_path = self.root / 'API.json'
        self.workflow_path.write_text(json.dumps(graph()), encoding='utf-8')
        self.controller = DashboardController(Settings(workflow_path=str(self.workflow_path)), auto_scan=False, monitor=False)
        self.addCleanup(self.controller.close)
        self.controller.start_scan(str(self.root))
        deadline=time.monotonic()+3
        while self.controller.scanning and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertFalse(self.controller.scanning, self.controller.last_error)

    def test_recurse_and_select(self):
        s=self.controller.snapshot()
        self.assertEqual(s['counts']['total'],2)
        self.assertEqual(s['counts']['selected'],2)
        self.controller.select('none')
        self.assertEqual(self.controller.snapshot()['counts']['selected'],0)
        self.controller.toggle('Tree/Baum.png', True)
        self.assertEqual(self.controller.snapshot()['counts']['selected'],1)
        self.controller.select('visible', 'stein', 'all')
        self.assertEqual(self.controller.snapshot()['counts']['selected'],2)
        self.assertEqual(self.controller.snapshot(search='baum')['counts']['filtered'],1)

    def test_settings_workflow_and_path_rules(self):
        self.controller.update_settings({'cooldown_seconds':8, 'cpu_limit':84, 'backup_on_replace':False})
        self.assertEqual(self.controller.settings.cooldown_seconds,8)
        self.assertEqual(self.controller.settings.cpu_limit,84)
        with self.assertRaises(ValueError):self.controller.update_settings({'root':'/other'})
        with self.assertRaises(ValueError):self.controller.update_settings({'comfy_url':'http://8.8.8.8'})
        info=self.controller.inspect_workflow(str(self.workflow_path))
        self.assertEqual(info['nodes'],3)
        self.assertEqual(info['images'][0]['id'],'1')
        self.controller.save_workflow({'workflow_path':str(self.workflow_path),'image_node':'1','output_node':'3'})
        self.assertEqual(self.controller.settings.output_node,'3')

    def test_unknown_preview_denied(self):
        with self.assertRaises(FileNotFoundError):self.controller.preview('../private.png')
        # The preview must only accept exact rel paths from the scanned list.
        with self.assertRaises(FileNotFoundError):self.controller.preview('Tree/../../API.json')

    def test_runner_start_receives_replace_mode(self):
        seen=[]
        class FakeRunner:
            def __init__(self, settings, notify):
                self.root=Path(settings.root).resolve()
                self.store=JobStore(self.root)
                self.running=False
            def start(self, rels, replace=False): seen.append((rels,replace))
        with patch('assetfoundry.dashboard.BatchRunner', FakeRunner):
            self.controller.start_batch(replace=True)
        self.assertEqual(seen[0],(['Stein.png','Tree/Baum.png'], True))


class ServerTests(DashboardTests):
    def setUp(self):
        super().setUp()
        self.server=DashboardServer(self.controller,0)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def get(self,path,auth=True,origin=None):
        headers={}
        if auth: headers['X-AssetFoundry-Token']=self.server.auth_token
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.base+path,headers=headers)
        with urllib.request.urlopen(req,timeout=3) as r:
            return r.status,r.headers,r.read()

    def post(self,path,obj,auth=True,origin=None):
        headers={'Content-Type':'application/json'}
        if auth:headers['X-AssetFoundry-Token']=self.server.auth_token
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.base+path,data=json.dumps(obj).encode(),headers=headers)
        with urllib.request.urlopen(req,timeout=3) as r:
            return json.loads(r.read())

    def test_html_and_static_ui(self):
        _,header,body=self.get('/?token='+self.server.auth_token,auth=False)
        self.assertIn(b'AssetFoundry',body)
        self.assertIn(b'Asset-Bibliothek',body)
        self.assertIn("connect-src 'self'",header.get('Content-Security-Policy'))
        _,_,js=self.get('/static/app.js',auth=False)
        self.assertIn(b'/api/start',js)
        _,_,css=self.get('/static/styles.css',auth=False)
        self.assertIn(b'.sidebar',css)

    def test_api_auth_and_cross_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get('/api/state',auth=False)
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get('/api/state',origin='https://attacker.example')
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post('/api/select',{'mode':'none'},origin='https://attacker.example')
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get('/?token=invalid',auth=False)
        self.assertEqual(e.exception.code,403)

    def test_api_scan_workflow_and_settings(self):
        code,_,body=self.get('/api/state')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['counts']['total'],2)
        self.assertTrue(self.post('/api/select',{'mode':'none'})['ok'])
        self.assertEqual(json.loads(self.get('/api/state')[2])['counts']['selected'],0)
        self.assertTrue(self.post('/api/toggle',{'rel':'Stein.png','selected':True})['ok'])
        self.assertEqual(json.loads(self.get('/api/state')[2])['counts']['selected'],1)
        info=json.loads(self.get('/api/workflow?path='+urllib.parse.quote(str(self.workflow_path)))[2])
        self.assertEqual(info['nodes'],3)
        self.assertTrue(self.post('/api/workflow',{'workflow_path':str(self.workflow_path),'image_node':'1','output_node':'3'})['ok'])
        self.assertTrue(self.post('/api/settings',{'cooldown_seconds':5.5})['ok'])
        self.assertEqual(self.controller.settings.cooldown_seconds,5.5)
        with self.assertRaises(urllib.error.HTTPError) as ex:
            self.post('/api/settings',{'comfy_url':'http://bad.example'})
        self.assertEqual(ex.exception.code,400)

    def test_preview_requires_exact_scanned_asset(self):
        with self.assertRaises(urllib.error.HTTPError) as ex:
            self.get('/api/preview?rel=API.json')
        self.assertEqual(ex.exception.code,404)
        with self.assertRaises(urllib.error.HTTPError) as ex:
            self.get('/api/preview?rel=Tree%2F..%2F..%2FAPI.json')
        self.assertEqual(ex.exception.code,404)


if __name__=='__main__':
    unittest.main()

class SelectionRescanTests(DashboardTests):
    def test_explicit_empty_selection_remains_empty_after_rescan(self):
        self.controller.select('none')
        self.controller.start_scan(str(self.root))
        deadline=time.monotonic()+3
        while self.controller.scanning and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(self.controller.snapshot()['counts']['selected'],0)
