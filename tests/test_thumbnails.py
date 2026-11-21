"""Regression tests for compact PNG thumbnails, caching and API access control."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:
    Image = None

from assetfoundry.dashboard import DashboardController
from assetfoundry.models import Settings
from assetfoundry.thumbnails import ThumbnailService
from assetfoundry.webapp import DashboardServer


def png_bytes(color=(255, 10, 30, 0), size=(800, 420)):
    if Image is None:
        raise unittest.SkipTest('Pillow nicht installiert')
    image = Image.new('RGBA', size, color)
    data = BytesIO()
    image.save(data, 'PNG')
    return data.getvalue()


@unittest.skipIf(Image is None, 'Pillow nicht installiert')
class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'app-config')})
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.root / 'nested').mkdir()
        self.image_path = self.root / 'nested' / 'Transparent.png'
        self.image_path.write_bytes(png_bytes())
        self.controller = DashboardController(Settings(), auto_scan=False, monitor=False)
        self.addCleanup(self.controller.close)
        self.controller.start_scan(str(self.root))
        deadline = time.monotonic() + 3
        while self.controller.scanning and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.controller.scanning, self.controller.last_error)

    def test_small_image_preserves_alpha_and_is_cached(self):
        first, mime = self.controller.thumbnail('nested/Transparent.png')
        self.assertEqual(mime, 'image/png')
        with Image.open(BytesIO(first)) as img:
            self.assertLessEqual(img.width, 128)
            self.assertLessEqual(img.height, 128)
            self.assertEqual(img.mode, 'RGBA')
            self.assertEqual(img.getpixel((0, 0))[3], 0)
        second, _ = self.controller.thumbnail('nested/Transparent.png')
        self.assertEqual(first, second)
        self.assertEqual(len(self.controller.thumbnails._cache), 1)
        self.assertLess(len(first), 50_000)
        self.assertIn('mtime_ns', self.controller.snapshot()['assets'][0])

    def test_invalid_and_unscanned_paths_are_denied(self):
        with self.assertRaises(FileNotFoundError):
            self.controller.thumbnail('../secrets.png')
        with self.assertRaises(FileNotFoundError):
            self.controller.thumbnail('Transparent.png')
        self.image_path.write_bytes(b'not png')
        with self.assertRaises(ValueError):
            self.controller.thumbnail('nested/Transparent.png')

    def test_cache_invalidates_when_image_changes(self):
        service = ThumbnailService()
        one, _ = service.get(self.image_path)
        self.image_path.write_bytes(png_bytes(color=(10, 255, 99, 255)))
        os.utime(self.image_path, ns=(time.time_ns(), time.time_ns()))
        two, _ = service.get(self.image_path)
        self.assertNotEqual(one, two)
        self.assertEqual(len(service._cache), 1)

    def test_cache_is_bounded(self):
        service = ThumbnailService(max_entries=2)
        for idx in range(3):
            path = self.root / f'{idx}.png'
            path.write_bytes(png_bytes(color=(idx, 1, 2, 255)))
            service.get(path)
        self.assertEqual(len(service._cache), 2)

    def test_loopback_thumbnail_auth_and_mime(self):
        server = DashboardServer(self.controller, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/api/thumbnail?rel=' + urllib.parse.quote('nested/Transparent.png', safe='')
        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(url, timeout=3)
        self.assertEqual(unauthorized.exception.code, 403)
        req = urllib.request.Request(url, headers={'X-AssetFoundry-Token': server.auth_token})
        with urllib.request.urlopen(req, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.headers['Content-Type'].startswith('image/png'))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with Image.open(BytesIO(response.read())) as image:
                self.assertLessEqual(image.width, 128)
        unknown = urllib.request.Request(url.replace('nested%2FTransparent.png', '..%2Fsecret.png'),
                                         headers={'X-AssetFoundry-Token': server.auth_token})
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(unknown, timeout=3)
        self.assertEqual(missing.exception.code, 404)

if __name__ == '__main__':
    unittest.main()
