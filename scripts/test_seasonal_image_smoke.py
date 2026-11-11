#!/usr/bin/env python3
"""Regression for Docker internal networks without a default gateway."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import seasonal_image_smoke as smoke


class InternalNetworkSmokeTest(unittest.TestCase):
    def test_internal_only_network_uses_private_address_without_publishing(self):
        revision = 'a' * 40
        commands = []

        def docker(*args):
            commands.append(args)
            if args[:2] == ('image', 'inspect'):
                return json.dumps([{'Id': 'unit-image', 'Config': {'Labels': {
                    'org.opencontainers.image.revision': revision}}}])
            if args[0] == 'inspect':
                name = args[1]
                return json.dumps([{'State': {'Running': True}, 'NetworkSettings': {
                    'Ports': {}, 'Networks': {name: {
                        'IPAddress': '172.29.0.2', 'Gateway': ''}}}}])
            if args[:2] == ('network', 'inspect'):
                return json.dumps([{'Internal': True, 'IPAM': {'Config': [{'Subnet': '172.29.0.0/16'}]}}])
            return ''

        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            with patch.object(smoke, 'docker', side_effect=docker):
                with smoke.server(SimpleNamespace(binary=None, image='velvet-seasonal:sha-' + revision), Path(directory)) as base:
                    self.assertEqual(base, 'http://172.29.0.2:8799')
        run = next(command for command in commands if command[0] == 'run')
        self.assertNotIn('--publish', run)
        self.assertIn('--network', run)
        self.assertTrue(any(command[:3] == ('network', 'create', '--internal') for command in commands))
        self.assertTrue(any(command[:2] == ('rm', '--force') for command in commands))
        self.assertTrue(any(command[:2] == ('network', 'rm') for command in commands))


if __name__ == '__main__':
    unittest.main()
