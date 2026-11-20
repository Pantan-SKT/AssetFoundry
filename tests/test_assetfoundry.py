"""Offline-Integrationstests ohne NVIDIA-GPU und ohne laufendes ComfyUI."""
from __future__ import annotations
import io
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from assetfoundry.assets import fingerprint, scan, validate_png, PNG_MAGIC
from assetfoundry.batch import BatchRunner
from assetfoundry.comfy import ComfyClient, ComfyError
from assetfoundry.models import Settings, Job, atomic_json, load_settings, save_settings
from assetfoundry.output import validate_glb, OutputManager
from assetfoundry.resources import Metrics, overload, cooldown, wait_for_resources
from assetfoundry.state import FolderLock, JobStore
from assetfoundry.workflow import Workflow


PNG = (PNG_MAGIC + bytes.fromhex('0000000d49484452000000010000000108060000001f15c489')
       + bytes.fromhex('0000000b49444154789c636000020000050001a5f64540')
       + bytes.fromhex('0000000049454e44ae426082'))


def glb_bytes():
    # Kleines geometrisches glTF-Testmodell mit einem Dreieck.
    binary = struct.pack('<9f', 0,0,0,1,0,0,0,1,0) + struct.pack('<3H', 0,1,2) + b'\x00\x00'
    obj = {
        'asset': {'version':'2.0'}, 'buffers':[{'byteLength':len(binary)}],
        'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':36,'target':34962},
                       {'buffer':0,'byteOffset':36,'byteLength':6,'target':34963}],
        'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3',
                      'min':[0,0,0], 'max':[1,1,0]},
                     {'bufferView':1,'componentType':5123,'count':3,'type':'SCALAR'}],
        'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1}]}],
        'nodes':[{'mesh':0}], 'scenes':[{'nodes':[0]}], 'scene':0,
    }
    payload = json.dumps(obj).encode('utf-8')
    payload += b' ' * (-len(payload)%4)
    total = 12 + 8 + len(payload) + 8 + len(binary)
    return (struct.pack('<III', 0x46546c67, 2, total) +
            struct.pack('<II', len(payload), 0x4E4F534A) + payload +
            struct.pack('<II', len(binary), 0x004E4942) + binary)


def mock_graph():
    return {'1':{'class_type':'LoadImage','inputs':{'image':'example.png'}},
            '4':{'class_type':'MakeMesh','inputs':{'image':['1',0]}},
            '8':{'class_type':'SaveGLB','inputs':{'mesh':['4',0], 'filename_prefix':'3d/ComfyUI'}}}


class TestResources(unittest.TestCase):
    def test_thresholds(self):
        s=Settings(cpu_limit=75,ram_limit=80,gpu_limit=95,vram_limit=95)
        m=Metrics(cpu=78,ram=74,gpu=99,vram=23)
        self.assertEqual(len(overload(m,s)),2)
        s.gate_resources=False
        self.assertEqual(overload(m,s),[])

    def test_missing_metrics_are_not_invented(self):
        self.assertEqual(overload(Metrics(),Settings()),[])

    def test_pause_during_cooldown(self):
        event=threading.Event();event.set()
        self.assertFalse(cooldown(100,event))

    def test_resource_wait(self):
        class MockMonitor:
            def __init__(self):self.n=0
            def sample(self):
                self.n += 1
                return Metrics(cpu=100 if self.n==1 else 0, ram=0)
        m=MockMonitor(); s=Settings(cpu_limit=80)
        # force timeout rather than waiting two seconds in test
        self.assertTrue(wait_for_resources(m,s,threading.Event()))
        self.assertEqual(m.n,2)


class TestScanOutput(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_nested_png_and_existing_glb(self):
        (self.root/'Nature'/'Trees').mkdir(parents=True)
        (self.root/'Nature'/'Trees'/'Farn.PNG').write_bytes(PNG)
        (self.root/'Natur.png').write_bytes(PNG)
        (self.root/'Natur.glb').write_bytes(glb_bytes())
        assets=scan(self.root)
        self.assertEqual(len(assets),2)
        self.assertEqual({a.status for a in assets},{'bereit','vorhanden'})
        self.assertEqual(next(a for a in assets if a.rel.endswith('Farn.PNG')).target.name,'Farn.glb')

    def test_invalid_existing_glb(self):
        (self.root/'Baum.png').write_bytes(PNG)
        (self.root/'Baum.glb').write_bytes(b'xyz')
        self.assertEqual(scan(self.root)[0].status,'ungültige GLB')

    def test_glb_validator(self):
        p=self.root/'x.glb';p.write_bytes(glb_bytes());self.assertTrue(validate_glb(p))
        data=bytearray(glb_bytes());data[0]=0;p.write_bytes(data)
        self.assertFalse(validate_glb(p))
        p.write_bytes(glb_bytes()[:-4]);self.assertFalse(validate_glb(p))

    def test_png_validation_and_fingerprint(self):
        p=self.root/'x.png';p.write_bytes(PNG);validate_png(p)
        h=fingerprint(p);p.write_bytes(b'not png')
        self.assertNotEqual(h,fingerprint(p))
        with self.assertRaises(ValueError):validate_png(p)

    def test_exclusive_publish_and_backup(self):
        target=self.root/'tree.glb';manager=OutputManager(self.root)
        manager.publish(target,io.BytesIO(glb_bytes()))
        self.assertTrue(validate_glb(target))
        with self.assertRaises(FileExistsError):manager.publish(target,io.BytesIO(glb_bytes()))
        manager.publish(target,io.BytesIO(glb_bytes()),replace=True,backup=True)
        backups=list((self.root/'.assetfoundry'/'backups').rglob('*.glb'))
        self.assertEqual(len(backups),1)
        self.assertTrue(validate_glb(backups[0]))

    def test_invalid_download_preserves_old(self):
        p=self.root/'good.glb';p.write_bytes(glb_bytes());old=p.read_bytes()
        with self.assertRaises(ValueError):
            OutputManager(self.root).publish(p,io.BytesIO(b'bad data'),replace=True)
        self.assertEqual(p.read_bytes(),old)
        self.assertFalse(list(self.root.glob('*.part')))

    def test_invalid_destination_target_link(self):
        other=self.root/'other.glb';other.write_bytes(glb_bytes())
        p=self.root/'evil.glb'
        try:p.symlink_to(other)
        except OSError:self.skipTest('No symlink permissions')
        with self.assertRaises(ValueError):OutputManager(self.root).publish(p,io.BytesIO(glb_bytes()),replace=True)

    def test_case_insensitive_name_collision(self):
        if os.name=='nt':self.skipTest('Windows forbids case-colliding names')
        (self.root/'X.png').write_bytes(PNG)
        (self.root/'x.PNG').write_bytes(PNG)
        self.assertTrue(all(a.status=='gesperrt' for a in scan(self.root)))

    def test_hide_state_and_backups(self):
        (self.root/'.assetfoundry'/'backups').mkdir(parents=True)
        (self.root/'.assetfoundry'/'backups'/'backup.png').write_bytes(PNG)
        self.assertEqual(scan(self.root),[])


class TestSettingsAndJobs(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_settings_round_trip(self):
        path=self.root/'config.json';s=Settings(cooldown_seconds=17,gpu_index=2)
        save_settings(s,path);self.assertEqual(load_settings(path).gpu_index,2)
        self.assertEqual(load_settings(path).cooldown_seconds,17)

    def test_remote_url_rejected(self):
        with self.assertRaises(ValueError):Settings(comfy_url='https://some-server.com').validate()
        with self.assertRaises(ValueError):Settings(comfy_url='http://192.168.1.22:8188').validate()

    def test_corrupt_state_is_not_reset(self):
        p=self.root/'.assetfoundry'/'jobs.json';p.parent.mkdir()
        p.write_text('{corrupt}',encoding='utf-8')
        with self.assertRaises(ValueError):JobStore(self.root)
        self.assertEqual(p.read_text(),'{corrupt}')

    def test_folder_lock_blocks(self):
        with FolderLock(self.root):
            with self.assertRaises(RuntimeError):
                with FolderLock(self.root):pass

    def test_job_persistence(self):
        st=JobStore(self.root);st.put(Job(rel='a/b.png',fingerprint='a'*64,prompt_id='uuid',status='läuft'))
        self.assertEqual(JobStore(self.root).jobs['a/b.png'].status,'läuft')


class TestWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'API.json'
        self.path.write_text(json.dumps(mock_graph()),encoding='utf-8')

    def test_api_mapping(self):
        wf=Workflow(self.path)
        image_node,output_node=wf.resolve()
        self.assertEqual((image_node,output_node),('1','8'))
        pid='12345678-1234-1234-1234-123456789abc'
        graph=wf.build('af_123.png',pid,image_node,output_node)
        self.assertEqual(graph['1']['inputs']['image'],'af_123.png')
        self.assertEqual(graph['8']['inputs']['filename_prefix'],'AssetFoundry/'+pid)
        self.assertEqual(wf.graph['1']['inputs']['image'],'example.png')

    def test_non_api_format_rejected(self):
        self.path.write_text(json.dumps({'nodes':[{'id':1}], 'links':[]}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'API-Format'):Workflow(self.path)

    def test_ambiguous_images_require_selection(self):
        raw=mock_graph();raw['10']={'class_type':'LoadImage','inputs':{'image':'b.png'}}
        self.path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Bild-Node'):Workflow(self.path).resolve()

    def test_disconnected_png_input_is_rejected(self):
        raw=mock_graph()
        raw['4']['inputs']['image'] = ['21',0]
        raw['21']={'class_type':'LoadImage','inputs':{'image':'other.png'}}
        self.path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Bild-Node'):
            Workflow(self.path).resolve('1','8')

    def test_missing_export_requires_selection(self):
        raw=mock_graph();raw['8']['class_type']='UnknownExporter'
        self.path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Export-Node'):Workflow(self.path).resolve()


class FakeComfy:
    def __init__(self):
        self.requests=[]
        self.fail_glb=False
        self.delay=0
        self.prompt_id=None
        self.mode='success'
        self.submit_calls=0


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass

    def json_answer(self,obj,status=200):
        data=json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)

    def do_GET(self):
        fake=self.server.fake
        if self.path.startswith('/system_stats'):
            return self.json_answer({'devices':[]})
        if self.path.startswith('/history/'):
            pid=self.path.split('/history/',1)[1]
            if pid != fake.prompt_id:
                return self.json_answer({})
            if fake.mode=='missing':
                return self.json_answer({})
            if fake.mode=='error':
                return self.json_answer({pid:{'status':{'status_str':'error','completed':False,'messages':['error']}}})
            return self.json_answer({pid:{'status':{'status_str':'success','completed':True},
                    'outputs':{'8':{'3d':[{'filename':'test.glb','subfolder':'3d','type':'output'}]}}}})
        if self.path.startswith('/queue'):
            return self.json_answer({'queue_pending':[],'queue_running':[]})
        if self.path.startswith('/view?'):
            data=b'bad data' if fake.fail_glb else glb_bytes()
            self.send_response(200);self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
            return
        self.json_answer({},404)

    def do_POST(self):
        fake=self.server.fake
        content=self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path=='/upload/image':
            fake.requests.append('upload')
            self.assert_png(content)
            return self.json_answer({'name':'test.png','subfolder':'','type':'input'})
        if self.path=='/prompt':
            obj=json.loads(content)
            fake.requests.append('prompt')
            fake.submit_calls += 1
            fake.prompt_id=obj['prompt_id']
            if obj['prompt']['1']['inputs']['image']!='test.png':
                return self.json_answer({'error':'no injected image'},400)
            return self.json_answer({'prompt_id':fake.prompt_id,'number':1})
        return self.json_answer({},404)

    def assert_png(self,content):
        assert PNG_MAGIC in content


class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'asset').mkdir()
        (self.root/'asset'/'baum.png').write_bytes(PNG)
        self.workflow=self.root/'API.json'
        self.workflow.write_text(json.dumps(mock_graph()),encoding='utf-8')
        fake=FakeComfy()
        self.server=ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.server.fake=fake
        self.fake=fake
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.settings=Settings(root=str(self.root), workflow_path=str(self.workflow),
                               comfy_url=f'http://127.0.0.1:{self.server.server_address[1]}',
                               cooldown_seconds=0,gate_resources=False,poll_seconds=0.5)
        self.messages=[]

    def run_batch(self,selected=None,replace=False):
        b=BatchRunner(self.settings,lambda k,v:self.messages.append((k,v)))
        b.start(selected or ['asset/baum.png'],replace)
        b.worker.join(timeout=10)
        self.assertFalse(b.running, 'Batch soll terminieren')
        return b

    def test_real_http_api_upload_generate_download(self):
        b=self.run_batch()
        target=self.root/'asset'/'baum.glb'
        self.assertTrue(validate_glb(target))
        self.assertEqual(self.fake.requests,['upload','prompt'])
        self.assertEqual(b.store.jobs['asset/baum.png'].status,'fertig')

    def test_existing_skip_and_opt_in_replace_backup(self):
        p=self.root/'asset'/'baum.glb';p.write_bytes(glb_bytes())
        self.run_batch(replace=False)
        self.assertEqual(self.fake.submit_calls,0)
        self.run_batch(replace=True)
        self.assertEqual(self.fake.submit_calls,1)
        self.assertTrue(list((self.root/'.assetfoundry'/'backups').rglob('*.glb')))

    def test_invalid_remote_glb_keeps_original(self):
        p=self.root/'asset'/'baum.glb';p.write_bytes(glb_bytes());old=p.read_bytes()
        self.fake.fail_glb=True
        b=self.run_batch(replace=True)
        self.assertEqual(p.read_bytes(),old)
        self.assertEqual(b.store.jobs['asset/baum.png'].status,'ausgabe-fehler')
        self.assertFalse(list((self.root/'asset').glob('*.part')))

    def test_resume_history_no_duplicate_submission(self):
        pid='11111111-2222-3333-4444-555555555555'
        self.fake.prompt_id=pid
        st=JobStore(self.root)
        st.put(Job(rel='asset/baum.png',fingerprint=fingerprint(self.root/'asset'/'baum.png'),
                   status='läuft',prompt_id=pid,replace=False))
        b=self.run_batch()
        self.assertEqual(self.fake.submit_calls,0)
        self.assertEqual(b.store.jobs['asset/baum.png'].status,'fertig')

    def test_missing_prompt_not_blindly_resubmitted(self):
        st=JobStore(self.root)
        st.put(Job(rel='asset/baum.png',fingerprint=fingerprint(self.root/'asset'/'baum.png'),
                   status='unklar',prompt_id='11111111-2222-3333-4444-555555555555'))
        b=self.run_batch()
        self.assertEqual(self.fake.submit_calls,0)
        self.assertEqual(b.store.jobs['asset/baum.png'].status,'unklar')

    def test_png_changed_blocks_resubmission(self):
        st=JobStore(self.root)
        st.put(Job(rel='asset/baum.png',fingerprint='0'*64,status='läuft',prompt_id='pid'))
        b=self.run_batch()
        self.assertEqual(b.store.jobs['asset/baum.png'].status,'bild-geändert')
        self.assertEqual(self.fake.submit_calls,0)

    def test_reset_allows_new_submission(self):
        st=JobStore(self.root)
        st.put(Job(rel='asset/baum.png',fingerprint='0'*64,status='fehler',prompt_id='old'))
        b=BatchRunner(self.settings)
        b.reset(['asset/baum.png'])
        self.assertEqual(b.store.jobs['asset/baum.png'].status,'bereit')
        self.run_batch()
        self.assertEqual(self.fake.submit_calls,1)

    def test_missing_workflow_has_actionable_error(self):
        self.settings.workflow_path=str(self.root/'missing.json')
        b=BatchRunner(self.settings)
        with self.assertRaisesRegex(ValueError,'workflows/API.json'):
            b.start(['asset/baum.png'])

    def test_history_node_without_glb_fails_explicitly(self):
        with self.assertRaisesRegex(ComfyError,'genau eine'):
            ComfyClient.find_glb({'outputs':{'8':{'3d':[]}}},'8')

if __name__=='__main__':
    unittest.main()
