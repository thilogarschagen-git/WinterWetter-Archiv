"""Exercise static export against isolated archive data."""
import os
import json
from pathlib import Path
import tempfile
import unittest

class ExportTest(unittest.TestCase):
    def test_export(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ['WW_DATA_DIR'] = root + '/data'
            import publish
            output = Path(root) / 'site'
            self.assertEqual(publish.export_site(output)['status'], 'not_started')
            with publish.weather.con() as db:
                db.execute('INSERT INTO weather VALUES (?,?,?,?,?,?,?,?,?,?)', ('remscheid','2025-12-01T12:00','2025-12-01T13:00',-2,1,2,0,-1,71,publish.weather.URL))
                db.execute('INSERT INTO runs(started_at,weather_count,warning_count,backup,errors) VALUES(?,?,?,?,?)', ('2026-11-04T06:17:00+01:00',1,0,'/private/backup',json.dumps(['DWD <unavailable>'])))
            publish.export_site(output)
            page = (output/'index.html').read_text()
            self.assertIn('Abruf mit Fehlern',page)
            self.assertIn('DWD &lt;unavailable&gt;',page)
            self.assertIn('WinterWetter-remscheid-2025-12.pdf',page)
            self.assertTrue((output/'WinterWetter-remscheid-2025-12.pdf').read_bytes().startswith(b'%PDF-'))
            self.assertNotIn('backup',json.loads((output/'status.json').read_text()))
            with publish.weather.con() as db:
                db.execute('UPDATE runs SET errors=?',('[]',))
            publish.export_site(output)
            self.assertIn('Abruf ohne gemeldete Fehler.',(output/'index.html').read_text())

if __name__ == '__main__':
    unittest.main()
