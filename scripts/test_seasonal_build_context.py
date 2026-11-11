#!/usr/bin/env python3
"""Exercise the artifact workflow's actual clean build-context staging."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SeasonalBuildContextTest(unittest.TestCase):
    def test_runtime_embedded_inputs_are_staged_and_allowlisted(self):
        workflow = (ROOT / '.github/workflows/seasonal-artifacts.yml').read_text()
        staging = workflow.split('          BUILD_CONTEXT=', 1)[1].split('          docker build', 1)[0]
        staging = 'BUILD_CONTEXT=' + staging
        with tempfile.TemporaryDirectory(prefix='seasonal-context-test-', dir=os.environ['TMPDIR']) as directory:
            subprocess.run(['bash', '-eu'], input=staging, text=True, cwd=ROOT,
                           env={**os.environ, 'RUNNER_TEMP': directory}, check=True)
            contexts = list(Path(directory).glob('seasonal-build-context.*'))
            self.assertEqual(len(contexts), 1)
            context = contexts[0]
            embedded = [
                'seasonal.default.json', 'src/epg_mappings.json',
                'src/data/spongebob_episode_groups.json',
                'admin/index.html', 'admin/admin.css', 'admin/admin.js',
            ]
            allowlist = (ROOT / 'backend/.dockerignore').read_text().splitlines()
            for relative in embedded:
                with self.subTest(input=relative):
                    self.assertTrue((context / relative).is_file(), f'Missing staged runtime input: {relative}')
                    self.assertEqual((context / relative).read_bytes(), (ROOT / 'backend' / relative).read_bytes())
                    self.assertIn('!' + relative if not relative.startswith('admin/') else '!admin/*.' + relative.rsplit('.', 1)[1], allowlist)
            self.assertIn('!src/data/', allowlist)
            self.assertFalse((context / 'velvet.toml').exists())
            self.assertFalse((context / 'target').exists())
            self.assertFalse((context / '.env').exists())


if __name__ == '__main__':
    unittest.main()
