"""Regression tests for persistent batch timings, selection and error export."""
from __future__ import annotations
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from zipfile import ZipFile

from assetfoundry.dashboard import DashboardController
from assetfoundry.batch import BatchRunner
from assetfoundry.models import Settings, Job
from assetfoundry.state import JobStore, RunStore
from assetfoundry.webapp import DashboardServer

PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489')


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.patch=patch.dict(os.environ, {'LOCALAPPDATA':str(self.root/'private-state')})
        self.patch.start(); self.addCleanup(self.patch.stop)
        (self.root/'Tree').mkdir()
        (self.root/'Tree'/'Baum.png').write_bytes(PNG)
        (self.root/'Stein.png').write_bytes(PNG)
        self.workflow=self.root/'API.json'
        self.workflow.write_text(json.dumps({
            '1': {'class_type':'LoadImage', 'inputs':{'image':'x.png'}},
            '2': {'class_type':'SaveGLB','inputs':{'mesh':['1',0], 'filename_prefix':'prefix'}}
        }))
        self.controller=DashboardController(Settings(workflow_path=str(self.workflow)),auto_scan=False,monitor=False)
        self.addCleanup(self.controller.close)
        self.scan()

    def scan(self):
        self.controller.start_scan(str(self.root))
        limit=time.monotonic()+4
        while self.controller.scanning and time.monotonic()<limit:
            time.sleep(.01)
        self.assertFalse(self.controller.scanning,self.controller.last_error)

    def test_visible_deselect_all_even_with_search_and_pagination(self):
        self.controller.select('none')
        self.assertEqual(self.controller.snapshot()['counts']['selected'],0)
        self.controller.select('visible','stein','all')
        self.assertEqual(self.controller.snapshot()['counts']['selected'],1)
        self.controller.select('missing')
        self.assertEqual(self.controller.snapshot()['counts']['selected'],2)
        self.controller.select('none','baum','missing')
        self.assertEqual(self.controller.snapshot()['counts']['selected'],0)

    def test_batch_run_persists_across_restart_and_completed_time_freezes(self):
        c=self.controller
        class FakeRunner:
            def __init__(self, settings, notify):
                self.root=Path(settings.root).resolve()
                self.store=JobStore(self.root)
                self.running=False
                self.stop=threading.Event()
            def start(self, rels, replace=False): pass
        with patch('assetfoundry.dashboard.BatchRunner',FakeRunner):
            c.start_batch()
        self.assertGreater(c.batch_started_at,0)
        self.assertTrue(RunStore(self.root).path.exists())
        started=c.batch_started_at
        c._notify('finished','Batch fertig.')
        data=c.snapshot()
        self.assertEqual(data['batch']['outcome'],'completed')
        self.assertGreaterEqual(data['batch']['ended_at'],started)
        elapsed=data['batch']['elapsed_seconds']
        time.sleep(.025)
        self.assertEqual(c.snapshot()['batch']['elapsed_seconds'],elapsed)
        restored=DashboardController(Settings(root=str(self.root),workflow_path=str(self.workflow)),monitor=False)
        self.addCleanup(restored.close)
        limit=time.monotonic()+4
        while restored.scanning and time.monotonic()<limit: time.sleep(.01)
        self.assertEqual(restored.snapshot()['batch']['started_at'],started)
        self.assertEqual(restored.snapshot()['batch']['ended_at'],data['batch']['ended_at'])

    def test_job_timing_and_hidden_running_job(self):
        c=self.controller
        c.batch_started_at=time.time()-25
        c.batch_rels={'Tree/Baum.png'}
        c.batch_outcome='running'
        class FakeRunner:
            root=self.root
            running=True
            store=c.store
        c.runner=FakeRunner()
        c.store.put(Job(rel='Tree/Baum.png',fingerprint='1',status='läuft',started=time.time()-16))
        snap=c.snapshot(search='Stein')
        self.assertEqual(len(snap['assets']),1)
        self.assertEqual(snap['batch']['current_rel'],'Tree/Baum.png')
        self.assertLess(abs(snap['batch']['current_started_at']-(time.time()-16)),1)
        c.runner=None

    def test_diagnostic_zip_masks_root_but_keeps_actionable_details(self):
        c=self.controller
        c.log('Fehler in '+str(self.root/'Tree'/'Baum.png')+' api_key=SECRET123','error')
        c.store.put(Job(rel='Tree/Baum.png',fingerprint='1',status='fehler',message='ComfyUI node error',started=time.time()-20,finished=time.time()))
        zip_bytes=c.diagnostics()
        self.assertLess(len(zip_bytes),3*1024*1024)
        with ZipFile(BytesIO(zip_bytes)) as z:
            self.assertEqual(set(z.namelist()),{'diagnose.json','jobs.json','recent_events.json','activity.log','errors.log','LIESMICH.txt'})
            metadata=json.loads(z.read('diagnose.json'))
            self.assertEqual(metadata['version'],'1.2.0')
            self.assertEqual(metadata['privacy'],'absolute_pfade_verdeckt')
            self.assertIn('ComfyUI node error',z.read('jobs.json').decode())
            for name in z.namelist():
                text=z.read(name).decode()
                self.assertNotIn(str(self.root),text)
                self.assertNotIn('SECRET123',text)
                self.assertNotIn(PNG.decode('latin1'),text)
        with ZipFile(BytesIO(c.diagnostics(include_paths=True))) as z:
            self.assertEqual(json.loads(z.read('diagnose.json'))['settings']['root'],str(self.root))
        with self.assertRaises(ValueError): c.diagnostics(include_paths='true')

    def test_comfyui_node_error_is_saved_for_diagnostic_zip(self):
        c=self.controller
        runner=BatchRunner(c.settings,c._notify)
        c.runner=runner
        job=Job(rel='Tree/Baum.png',fingerprint='1',status='läuft',prompt_id='abc-test',started=time.time()-3)
        runner.store.put(job)
        class BrokenClient:
            def lookup(self, prompt_id):
                return 'error', {'status': {'messages': [['execution_error', {'node_id': '17', 'exception_message': 'custom-node-failure-vram'}]]}}
        result=runner._wait(BrokenClient(),job)
        self.assertIsNone(result)
        self.assertEqual(job.status,'fehler')
        self.assertGreaterEqual(job.finished,job.started)
        dump=c.diagnostics()
        with ZipFile(BytesIO(dump)) as z:
            self.assertIn('custom-node-failure-vram',z.read('errors.log').decode('utf8'))
            self.assertIn('fehler',z.read('jobs.json').decode('utf8'))

    def test_diagnostic_server_authorization_and_download(self):
        server=DashboardServer(self.controller, 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        if True:
            target=f'http://127.0.0.1:{server.server_port}/api/diagnostics'
            def req(headers, payload):
                return urllib.request.Request(target,headers=headers,data=json.dumps(payload).encode())
            with self.assertRaises(urllib.error.HTTPError) as bad:
                urllib.request.urlopen(req({'Content-Type':'application/json'},{}),timeout=3)
            self.assertEqual(bad.exception.code,403)
            headers={'Content-Type':'application/json','X-AssetFoundry-Token':server.auth_token}
            with self.assertRaises(urllib.error.HTTPError) as invalid:
                urllib.request.urlopen(req(headers,{'include_paths':'true'}),timeout=3)
            self.assertEqual(invalid.exception.code,400)
            with urllib.request.urlopen(req(headers,{'include_paths':False}),timeout=3) as res:
                self.assertEqual(res.getheader('Content-Type'),'application/zip')
                self.assertIn('.zip',res.getheader('Content-Disposition'))
                with ZipFile(BytesIO(res.read())) as zipfile:
                    self.assertIn('jobs.json',zipfile.namelist())

if __name__=='__main__':
    unittest.main()
