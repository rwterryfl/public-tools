#!/usr/bin/env python3
"""Count matching OpenShift pod log lines per minute.

Follows the logs of a pod with ``oc logs -f --timestamps``, matches each line
against a regular expression, and prints the number of matching lines for every
minute until interrupted with Ctrl+C. Lines are assigned to minutes using the
timestamp OpenShift records when the container wrote the line, converted to
local time.
"""

import argparse
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import IO, List, Optional, Sequence, Tuple

MINUTE = timedelta(minutes=1)
MINUTE_FORMAT = "%Y-%m-%d %H:%M"
EMIT_INTERVAL_SECONDS = 0.5
DEFAULT_GRACE_SECONDS = 2.0
TERMINATE_TIMEOUT_SECONDS = 5

# (minute, count, partial)
MinuteResult = Tuple[datetime, int, bool]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse and validate command-line arguments.

    Exits with code 2 on missing arguments or an invalid pattern.
    """
    parser = argparse.ArgumentParser(
        description="Follow an OpenShift pod's logs and count lines matching "
        "a pattern for every minute.",
    )
    parser.add_argument("-p", "--pod", required=True, help="pod name")
    parser.add_argument("-n", "--namespace", required=True, help="namespace (project)")
    parser.add_argument(
        "-e", "--pattern", required=True, help="regular expression to match (like grep -E)"
    )
    parser.add_argument("-c", "--container", help="container name for multi-container pods")
    parser.add_argument(
        "-i", "--ignore-case", action="store_true", help="match case-insensitively"
    )
    parser.add_argument(
        "--since", default="1s", help="value passed to 'oc logs --since' (default: 1s)"
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=DEFAULT_GRACE_SECONDS,
        metavar="SECONDS",
        help="how long to wait after a minute ends for its log lines to arrive "
        f"before reporting it (default: {DEFAULT_GRACE_SECONDS:g})",
    )
    args = parser.parse_args(argv)
    if args.grace < 0:
        parser.error("--grace must not be negative")

    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        args.regex = re.compile(args.pattern, flags)
    except re.error as exc:
        parser.error(f"invalid pattern {args.pattern!r}: {exc}")
    return args


def build_oc_command(args: argparse.Namespace) -> List[str]:
    """Build the ``oc logs`` argument list for the given arguments."""
    cmd = [
        "oc", "logs", "-f", "--timestamps",
        args.pod, "-n", args.namespace, f"--since={args.since}",
    ]
    if args.container:
        cmd += ["-c", args.container]
    return cmd


def truncate_to_minute(moment: datetime) -> datetime:
    """Return ``moment`` with seconds and microseconds removed."""
    return moment.replace(second=0, microsecond=0)


def parse_timestamp(line: str) -> Tuple[Optional[datetime], str]:
    """Split a line from ``oc logs --timestamps`` into its timestamp and text.

    The prefix is an RFC 3339 timestamp such as
    ``2026-11-01T14:05:12.345678901Z``. It is returned as a naive datetime in
    local time. If the line has no parsable prefix, returns ``(None, line)``.
    """
    stamp, sep, text = line.partition(" ")
    if not sep or len(stamp) < 20 or stamp[10] != "T":
        return None, line
    try:
        moment = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
        # Fractional seconds are ignored: only the minute matters.
        zone = stamp[19:].lstrip(".0123456789")
        if zone == "Z":
            offset = timedelta(0)
        elif zone[:1] in ("+", "-"):
            hours, minutes = zone[1:].split(":")
            offset = timedelta(hours=int(hours), minutes=int(minutes))
            if zone[0] == "-":
                offset = -offset
        else:
            return None, line
    except (ValueError, IndexError):
        return None, line
    local = moment.replace(tzinfo=timezone(offset)).astimezone()
    return local.replace(tzinfo=None), text


def format_result(result: MinuteResult) -> str:
    """Format a per-minute result as an output line."""
    minute, count, partial = result
    line = f"{minute.strftime(MINUTE_FORMAT)} : {count} lines/min"
    return line + " (partial)" if partial else line


class MinuteCounter:
    """Thread-safe counter that aggregates matches into one-minute buckets.

    Matches are recorded with their log timestamp. Minutes are closed either
    when a later-timestamped match arrives or when ``emit`` is called with a
    later time. Times are always passed in, so the class can be tested without
    a real clock. Completed minutes are queued until collected with ``emit``.

    A match whose minute has already been closed is "late": it is counted in
    the current minute, because the earlier minute has already been reported.
    """

    def __init__(self, start: datetime) -> None:
        self._lock = threading.Lock()
        self._minute = truncate_to_minute(start)
        self._count = 0
        # The minute the application starts in is only partially observed.
        self._partial = True
        self._completed: List[MinuteResult] = []
        self.total = 0
        self.late = 0

    def _advance(self, now: datetime) -> None:
        """Close every bucket before ``now``'s minute. Caller holds the lock."""
        current = truncate_to_minute(now)
        while self._minute < current:
            self._completed.append((self._minute, self._count, self._partial))
            self._minute += MINUTE
            self._count = 0
            self._partial = False

    def record(self, moment: datetime) -> None:
        """Record one matching line with log timestamp ``moment``."""
        with self._lock:
            self._advance(moment)
            if truncate_to_minute(moment) < self._minute:
                self.late += 1
            self._count += 1
            self.total += 1

    def emit(self, now: datetime) -> List[MinuteResult]:
        """Return all minutes completed as of ``now``, oldest first."""
        with self._lock:
            self._advance(now)
            completed, self._completed = self._completed, []
            return completed

    def flush(self, now: datetime) -> List[MinuteResult]:
        """Return completed minutes plus the current minute, marked partial."""
        with self._lock:
            self._advance(now)
            results = self._completed + [(self._minute, self._count, True)]
            self._completed = []
            return results


def print_results(results: List[MinuteResult]) -> None:
    """Print result lines to stdout and flush immediately."""
    for result in results:
        print(format_result(result))
    sys.stdout.flush()


def read_matches(stream: IO[str], regex: "re.Pattern[str]", counter: MinuteCounter) -> None:
    """Read log lines from ``stream`` and record each matching line.

    The timestamp prefix is removed before matching, so the pattern only sees
    the log text. Lines without a timestamp use the time they were read.
    """
    for line in stream:
        moment, text = parse_timestamp(line.rstrip("\n"))
        if regex.search(text):
            counter.record(moment or datetime.now())


def collect_stream(stream: IO[str], sink: List[str]) -> None:
    """Read all of ``stream`` into ``sink``."""
    sink.append(stream.read())


def stop_process(proc: "subprocess.Popen[str]") -> None:
    """Terminate ``proc``, killing it if it does not exit in time."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def format_elapsed(seconds: float) -> str:
    """Format a duration in seconds as HH:MM:SS."""
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def run(args: argparse.Namespace) -> int:
    """Follow the pod logs and print per-minute counts. Returns the exit code."""
    cmd = build_oc_command(args)
    try:
        # A new session keeps Ctrl+C from reaching oc directly; it is
        # terminated explicitly during shutdown instead.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError:
        print("Error: 'oc' command not found on PATH", file=sys.stderr)
        return 1

    target = f"pod {args.pod} in namespace {args.namespace}"
    if args.container:
        target += f", container {args.container}"
    print(
        f'Following {target}, pattern: "{args.pattern}" (Ctrl+C to stop)',
        file=sys.stderr,
        flush=True,
    )

    started = time.monotonic()
    counter = MinuteCounter(datetime.now())
    stderr_output: List[str] = []
    reader = threading.Thread(
        target=read_matches, args=(proc.stdout, args.regex, counter), daemon=True
    )
    stderr_reader = threading.Thread(
        target=collect_stream, args=(proc.stderr, stderr_output), daemon=True
    )
    reader.start()
    stderr_reader.start()

    grace = timedelta(seconds=args.grace)
    try:
        while reader.is_alive():
            print_results(counter.emit(datetime.now() - grace))
            reader.join(timeout=EMIT_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print_results(counter.flush(datetime.now()))
        summary = (
            f"Total matching lines: {counter.total}, "
            f"elapsed: {format_elapsed(time.monotonic() - started)}"
        )
        if counter.late:
            summary += (
                f" ({counter.late} lines arrived after their minute was "
                "reported and were counted in a later minute)"
            )
        print(summary, file=sys.stderr)
        stop_process(proc)
        return 0

    # oc exited on its own: pod missing, deleted, restarted, or access denied.
    print_results(counter.flush(datetime.now()))
    returncode = proc.wait()
    stderr_reader.join(timeout=TERMINATE_TIMEOUT_SECONDS)
    message = "".join(stderr_output).strip()
    if message:
        print(message, file=sys.stderr)
    print(f"Error: 'oc logs' exited with code {returncode}", file=sys.stderr)
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point."""
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
