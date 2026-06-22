import json
import os
import tempfile
import unittest

from opswatch.notify import Alert, FileChannel, Notifier


class FakeStore:
    def __init__(self):
        self.recorded = []

    def record_alert(self, source, severity, title, detail, created_at):
        self.recorded.append((source, severity, title))


class CapturingChannel:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


class NotifyTest(unittest.TestCase):
    def test_notify_records_and_fans_out_to_all_channels(self):
        store = FakeStore()
        c1, c2 = CapturingChannel(), CapturingChannel()
        notifier = Notifier(store, [c1, c2])
        notifier.notify(Alert(source="s", severity="critical", title="t", detail="d"))
        self.assertEqual(len(store.recorded), 1)
        self.assertEqual(len(c1.sent), 1)
        self.assertEqual(len(c2.sent), 1)

    def test_file_channel_writes_one_json_line_per_alert(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "alerts.log")
        ch = FileChannel(path)
        ch.send(Alert(source="s", severity="warning", title="t1", detail="d1"))
        ch.send(Alert(source="s", severity="recovered", title="t2", detail="d2"))
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["title"], "t1")
        self.assertEqual(lines[1]["severity"], "recovered")

    def test_alert_stamps_created_at_when_missing(self):
        alert = Alert(source="s", severity="critical", title="t", detail="d")
        self.assertGreater(alert.created_at, 0)


if __name__ == "__main__":
    unittest.main()
