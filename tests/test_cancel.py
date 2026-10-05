import sys
import threading
import time
import unittest

from creative_factory.remix import RemixError, command


class CancelTests(unittest.TestCase):
    def test_command_terminates_active_process_when_cancel_is_requested(self):
        cancel = threading.Event()
        timer = threading.Timer(.25, cancel.set)
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaisesRegex(RemixError, "anulowano"):
                command([sys.executable, "-c", "import time; time.sleep(8)"], cancel_event=cancel)
        finally:
            timer.cancel()

        self.assertLess(time.monotonic() - started, 4)


if __name__ == "__main__":
    unittest.main()
