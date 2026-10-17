import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('safe_source_fifo', Path(__file__).parents[1] / 'tools/safe_source.py')
safe_source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(safe_source)


class FifoTests(unittest.TestCase):
    def test_fifo_leaf_is_refused_not_blocked(self):
        with tempfile.TemporaryDirectory() as raw:
            td = os.path.realpath(raw)
            os.mkfifo(Path(td) / 'pipe')
            with self.assertRaises(ValueError):
                safe_source.open_source(td, 'pipe')

    def test_regular_file_still_opens(self):
        with tempfile.TemporaryDirectory() as raw:
            td = os.path.realpath(raw)
            (Path(td) / 'f').write_text('x')
            fd, _, _ = safe_source.open_source(td, 'f')
            os.close(fd)

    def test_stalled_open_times_out(self):
        import errno
        import time
        real = safe_source.os.open

        def hang(*a, **k):
            time.sleep(2)
            return real(*a, **k)
        with tempfile.TemporaryDirectory() as raw:
            td = os.path.realpath(raw)
            (Path(td) / 'f').write_text('x')
            safe_source.OPEN_TIMEOUT = 0.2
            try:
                safe_source.os.open, saved = hang, safe_source.os.open
                with self.assertRaises(OSError) as ctx:
                    safe_source.open_source(td, 'f')
            finally:
                safe_source.os.open = saved
                safe_source.OPEN_TIMEOUT = 45
            self.assertEqual(ctx.exception.errno, errno.ETIMEDOUT)


if __name__ == '__main__':
    unittest.main()
