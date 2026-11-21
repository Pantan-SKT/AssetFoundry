"""German/English localization coverage, persistence, and loopback API safety."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
from dataclasses import replace
import http.client
import threading
import unittest

from assetfoundry.dashboard import DashboardController
from assetfoundry.models import Settings, load_settings, save_settings
from assetfoundry.webapp import DashboardServer

LOCALES = Path(__file__).resolve().parents[1] / 'assetfoundry' / 'frontend' / 'locales'

class LocalizationTests(TestCase):
    def test_two_complete_catalogs_and_title(self):
        de = json.loads((LOCALES/'de.json').read_text('utf-8'))
        en = json.loads((LOCALES/'en.json').read_text('utf-8'))
        self.assertEqual(set(de), set(en))
        self.assertGreater(len(en), 270)
        self.assertEqual(en['Alle abwählen'], 'Deselect all')
        self.assertEqual(en['Fehler- & Diagnose-Dump'], 'Error & diagnostics dump')
        html = (LOCALES.parent/'index.html').read_text('utf-8')
        self.assertIn('<title>3D Asset Generator Batch</title>', html)
        self.assertIn('id="languageSelect"', html)
        self.assertIn('id="languageSetting"', html)
        self.assertIn('id="batchElapsed"', html)
        self.assertIn('id="clearSelection"', html)
        self.assertIn('data-command="diagnostics"', html)

    def test_settings_migration_and_validation(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'settings.json'
            path.write_text(json.dumps({'cooldown_seconds':5, 'comfy_url':'http://127.0.0.1:8188'}))
            prior = load_settings(path)
            self.assertEqual(prior.language, 'de')
            save_settings(replace(prior, language='en'), path)
            self.assertEqual(load_settings(path).language,'en')
        with self.assertRaises(ValueError):
            Settings(language='fr').validate()

    @mock.patch('assetfoundry.dashboard.save_settings')
    def test_language_switch_does_not_change_engine_or_runner(self, save):
        c=DashboardController(settings=Settings(cooldown_seconds=5),auto_scan=False,monitor=False)
        try:
            class FakeRunner:
                running=True
            runner=FakeRunner()
            c.runner=runner
            c.update_language('en')
            self.assertIs(c.runner,runner)
            self.assertEqual(c.settings.language,'en')
            self.assertEqual(c.settings.cooldown_seconds,5)
            self.assertEqual(c.settings.comfy_url,'http://127.0.0.1:8188')
            self.assertEqual(save.call_count,1)
            with self.assertRaises(ValueError): c.update_language('fr')
            with self.assertRaises(ValueError): c.update_language(None)
            self.assertEqual(c.settings.language,'en')
        finally: c.runner=None;c.close()

    def test_http_catalogs_language_and_reject_traversal(self):
        with mock.patch('assetfoundry.dashboard.save_settings'):
            c=DashboardController(settings=Settings(),auto_scan=False,monitor=False)
            srv=DashboardServer(controller=c)
            thread=threading.Thread(target=srv.serve_forever,daemon=True)
            thread.start()
            try:
                conn=http.client.HTTPConnection('127.0.0.1',srv.server_port,timeout=3)
                for locale in ('de','en'):
                    conn.request('GET','/static/locales/'+locale+'.json')
                    response=conn.getresponse()
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.getheader('X-Content-Type-Options'),'nosniff')
                    self.assertIn('Alle abwählen',json.loads(response.read().decode('utf-8')))
                conn.request('GET','/static/locales/secret.json')
                response = conn.getresponse()
                self.assertEqual(response.status,404)
                response.read()
                payload=json.dumps({'language':'en'})
                conn.request('POST','/api/language',payload,{'Content-Type':'application/json', 'X-AssetFoundry-Token':srv.auth_token})
                response=conn.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read())['language'],'en')
                self.assertEqual(c.settings.language,'en')
                conn.request('POST','/api/language',json.dumps({'language':'fr'}),{'Content-Type':'application/json','X-AssetFoundry-Token':srv.auth_token})
                response=conn.getresponse();self.assertEqual(response.status,400);response.read()
                conn.request('POST','/api/language',payload,{'Content-Type':'application/json'})
                response=conn.getresponse();self.assertEqual(response.status,403);response.read()
                conn.close()
            finally:
                srv.shutdown();thread.join(timeout=3);srv.server_close();c.close()

if __name__=='__main__': unittest.main()
