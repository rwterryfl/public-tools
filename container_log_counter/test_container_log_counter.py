"""Unit tests for container_log_counter."""

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import container_log_counter as clc

SCRIPT = Path(__file__).resolve().parent / "container_log_counter.py"


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 30, hour, minute, second)


class BuildOcCommandTest(unittest.TestCase):
    def test_without_container(self) -> None:
        args = clc.parse_args(["-p", "mypod", "-n", "myns", "-e", "ERROR"])
        self.assertEqual(
            clc.build_oc_command(args),
            ["oc", "logs", "-f", "--timestamps", "mypod", "-n", "myns", "--since=1s"],
        )

    def test_with_container_and_since(self) -> None:
        args = clc.parse_args(
            ["-p", "mypod", "-n", "myns", "-e", "ERROR", "-c", "app", "--since", "5m"]
        )
        self.assertEqual(
            clc.build_oc_command(args),
            [
                "oc", "logs", "-f", "--timestamps",
                "mypod", "-n", "myns", "--since=5m", "-c", "app",
            ],
        )


class ParseArgsTest(unittest.TestCase):
    def test_invalid_regex_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as ctx, open(os.devnull, "w") as devnull:
            stderr, sys.stderr = sys.stderr, devnull
            try:
                clc.parse_args(["-p", "pod", "-n", "ns", "-e", "("])
            finally:
                sys.stderr = stderr
        self.assertEqual(ctx.exception.code, 2)

    def test_ignore_case(self) -> None:
        args = clc.parse_args(["-p", "pod", "-n", "ns", "-e", "error", "-i"])
        self.assertTrue(args.regex.search("Some ERROR here"))

    def test_case_sensitive_by_default(self) -> None:
        args = clc.parse_args(["-p", "pod", "-n", "ns", "-e", "error"])
        self.assertIsNone(args.regex.search("Some ERROR here"))


def utc_stamp(moment: datetime) -> str:
    """Format a local naive datetime as an ``oc logs --timestamps`` prefix."""
    utc = moment.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


class ParseTimestampTest(unittest.TestCase):
    def test_utc_converted_to_local(self) -> None:
        local = at(14, 5, 12)
        moment, text = clc.parse_timestamp(f"{utc_stamp(local)} ERROR boom")
        self.assertEqual(moment, local)
        self.assertEqual(text, "ERROR boom")

    def test_numeric_offset(self) -> None:
        moment, _ = clc.parse_timestamp("2026-11-01T14:05:12.5+02:00 msg")
        expected = datetime(2026, 11, 1, 12, 5, 12, tzinfo=timezone.utc)
        self.assertEqual(moment, expected.astimezone().replace(tzinfo=None))

    def test_no_fraction(self) -> None:
        moment, text = clc.parse_timestamp("2026-11-01T14:05:12Z msg")
        self.assertIsNotNone(moment)
        self.assertEqual(text, "msg")

    def test_line_without_timestamp(self) -> None:
        for line in ["plain log line", "", "2026-11-01 not a stamp", "2026-11-01T14:05:12.1 x"]:
            self.assertEqual(clc.parse_timestamp(line), (None, line))


class MinuteCounterTest(unittest.TestCase):
    def test_emits_zero_count_minutes_for_gaps(self) -> None:
        counter = clc.MinuteCounter(at(10, 0, 30))
        counter.record(at(10, 0, 40))
        counter.record(at(10, 3, 5))
        self.assertEqual(
            counter.emit(at(10, 3, 10)),
            [(at(10, 0), 1, True), (at(10, 1), 0, False), (at(10, 2), 0, False)],
        )
        self.assertEqual(counter.emit(at(10, 3, 50)), [])
        self.assertEqual(counter.flush(at(10, 3, 55)), [(at(10, 3), 1, True)])
        self.assertEqual(counter.total, 2)

    def test_emit_without_lines(self) -> None:
        counter = clc.MinuteCounter(at(10, 0, 30))
        self.assertEqual(
            counter.emit(at(10, 2, 0)),
            [(at(10, 0), 0, True), (at(10, 1), 0, False)],
        )

    def test_record_advances_by_log_time(self) -> None:
        counter = clc.MinuteCounter(at(10, 0, 30))
        counter.record(at(10, 0, 50))
        counter.record(at(10, 1, 10))
        self.assertEqual(counter.emit(at(10, 0, 59)), [(at(10, 0), 1, True)])

    def test_late_line_counted_in_current_minute(self) -> None:
        counter = clc.MinuteCounter(at(10, 0, 30))
        self.assertEqual(counter.emit(at(10, 1, 0)), [(at(10, 0), 0, True)])
        counter.record(at(10, 0, 59))
        self.assertEqual(counter.late, 1)
        self.assertEqual(counter.flush(at(10, 1, 30)), [(at(10, 1), 1, True)])

    def test_timestamp_prefix_not_matched(self) -> None:
        counter = clc.MinuteCounter(at(10, 0))
        stamp = utc_stamp(at(10, 0, 5))
        stream = iter([f"{stamp} hello\n", f"{stamp} 2026 report\n"])
        clc.read_matches(stream, re.compile("2026"), counter)
        self.assertEqual(counter.total, 1)

    def test_read_matches_uses_log_timestamp(self) -> None:
        counter = clc.MinuteCounter(at(10, 0))
        stream = iter([f"{utc_stamp(at(10, 0, 5))} ERROR\n", f"{utc_stamp(at(10, 2, 5))} ERROR\n"])
        clc.read_matches(stream, re.compile("ERROR"), counter)
        self.assertEqual(
            counter.emit(at(10, 0)),
            [(at(10, 0), 1, True), (at(10, 1), 0, False)],
        )

    def test_line_with_multiple_occurrences_counts_once(self) -> None:
        counter = clc.MinuteCounter(at(10, 0))
        stream = iter(["ERROR ERROR ERROR\n", "ok\n", "ERROR\n"])
        clc.read_matches(stream, re.compile("ERROR"), counter)
        self.assertEqual(counter.total, 2)

    def test_format_result(self) -> None:
        self.assertEqual(
            clc.format_result((at(14, 5), 312, True)),
            "2026-10-30 14:05 : 312 lines/min (partial)",
        )
        self.assertEqual(
            clc.format_result((at(14, 6), 1180, False)),
            "2026-10-30 14:06 : 1180 lines/min",
        )


class FakeOcTest(unittest.TestCase):
    def test_run_against_fake_oc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_oc = Path(tmp) / "oc"
            fake_oc.write_text(
                "#!/bin/sh\n"
                "ts() { date -u +%Y-%m-%dT%H:%M:%S.000000000Z; }\n"
                "echo \"$(ts) INFO start\"\n"
                "echo \"$(ts) ERROR one\"\n"
                "echo \"$(ts) error two\"\n"
                "echo \"$(ts) ERROR three ERROR\"\n"
                "echo 'pod not found' >&2\n"
                "exit 3\n"
            )
            fake_oc.chmod(fake_oc.stat().st_mode | stat.S_IEXEC)
            env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "-p", "pod", "-n", "ns", "-e", "ERROR"],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 1)
        # Sum counts in case the run crossed a minute boundary.
        counts = re.findall(r" : (\d+) lines/min", result.stdout)
        self.assertTrue(counts)
        self.assertEqual(sum(int(c) for c in counts), 2)
        self.assertIn("pod not found", result.stderr)
        self.assertIn("exited with code 3", result.stderr)

    def test_oc_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "-p", "pod", "-n", "ns", "-e", "ERROR"],
                env=dict(os.environ, PATH=tmp),
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
