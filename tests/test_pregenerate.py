import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pregenerate


class ConcurrentGenerationTests(unittest.TestCase):
    def test_overlap_partial_save_and_resume(self):
        events = []

        class FakeServer:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def start_batch(self, dimension, task):
                events.append(('start', dimension, task))

            def batch_loaded(self, dimension, task):
                events.append(('poll', dimension, task))
                return True

            def save(self):
                events.append(('save',))

        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            args = ['--world', folder, '--dimension', 'all', '--radius', '32',
                    '--batch-size', '2', '--work-dir', str(work / 'control')]
            with patch.object(pregenerate, 'world_info', return_value={'seed': 1, 'spawn': [0, 64, 0]}), \
                 patch.object(pregenerate, 'find_java', return_value='java'), \
                 patch.object(pregenerate, 'ensure_server', return_value='server.jar'), \
                 patch.object(pregenerate, 'Server', FakeServer), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pregenerate.main(args + ['--max-batches', '5']), 0)
                self.assertEqual([e[0] for e in events[:3]], ['start'] * 3)
                checkpoints = [json.loads(p.read_text()) for p in (work / 'control').glob('progress-*.json')]
                self.assertEqual(sum(p['completed_batches'] for p in checkpoints), 5)
                self.assertFalse(json.loads((work / 'control/summary.json').read_text())['complete'])
                events.clear()
                self.assertEqual(pregenerate.main(args + ['--resume']), 0)
                # Four tasks per dimension; only the seven unfinished tasks restart.
                self.assertEqual(sum(e[0] == 'start' for e in events), 7)
                self.assertTrue(json.loads((work / 'control/summary.json').read_text())['complete'])
                events.clear()
                self.assertEqual(pregenerate.main(args + ['--resume']), 0)
                self.assertEqual(events, [])

    def test_unsaved_completion_is_not_checkpointed_on_failure(self):
        class FailingServer:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def start_batch(self, *args):
                pass

            def batch_loaded(self, dimension, task):
                if dimension == 'minecraft:the_end':
                    raise RuntimeError('interrupted')
                return True

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pregenerate, 'world_info', return_value={'seed': 1, 'spawn': [0, 64, 0]}), \
                 patch.object(pregenerate, 'find_java', return_value='java'), \
                 patch.object(pregenerate, 'ensure_server', return_value='server.jar'), \
                 patch.object(pregenerate, 'Server', FailingServer), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    pregenerate.main(['--world', folder, '--work-dir', folder, '--dimension', 'all', '--radius', '16'])
            self.assertEqual(list(Path(folder).glob('progress-*.json')), [])
