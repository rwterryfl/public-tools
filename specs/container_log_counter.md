# Container Log Counter — Requirements Specification

## 1. Purpose

`container_log_counter` is a Python command-line application that follows (tails) the logs of an OpenShift pod, counts the log lines that match a given grep-style pattern, and continuously reports the number of matching lines for each minute until the user interrupts it from the keyboard.

## 2. Scope

### In scope
- Following the logs of a single pod in a single namespace.
- Matching each log line against one regular-expression pattern.
- Aggregating matching lines into one-minute buckets, using the timestamp OpenShift records for each log line.
- Printing one result line to standard output per completed minute.
- Clean shutdown on keyboard interrupt (Ctrl+C).

### Out of scope
- Following multiple pods, label selectors, or entire deployments.
- Persisting results to files or databases (users can redirect stdout).
- Parsing structured log content (JSON fields, log levels, embedded timestamps, etc.).
- Authenticating to the cluster (the user is assumed to be logged in with `oc login`).

## 3. Runtime Environment

| Item | Requirement |
|------|-------------|
| Language | Python 3.9 or later |
| Dependencies | Python standard library only (no third-party packages) |
| External tools | OpenShift CLI (`oc`) available on `PATH` |
| Cluster access | An active `oc` session with permission to read logs of the target pod |
| Platform | Linux and macOS |

## 4. Command-Line Interface

### 4.1 Synopsis

```
container_log_counter.py -p POD -n NAMESPACE -e PATTERN [options]
```

### 4.2 Arguments

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `-p`, `--pod` | Yes | — | Name of the pod whose logs are followed. |
| `-n`, `--namespace` | Yes | — | Namespace (project) containing the pod. |
| `-e`, `--pattern` | Yes | — | Pattern to match against each log line. Interpreted as a Python regular expression (`re` module), matching anywhere in the line, like `grep -E`. |
| `-c`, `--container` | No | none | Container name, for pods that have more than one container. Passed to `oc logs -c`. |
| `-i`, `--ignore-case` | No | off | Match the pattern case-insensitively. |
| `--since` | No | `1s` | Value passed to `oc logs --since` so only new log lines are counted, not the pod's log history. |
| `--grace` | No | `2` | Seconds to wait after a minute ends for its log lines to arrive before reporting that minute. Must not be negative. |
| `-h`, `--help` | No | — | Print usage and exit. |

### 4.3 Validation
- If the pattern is not a valid regular expression, print an error message that includes the regex error and exit with code `2` before starting `oc`.
- If `--grace` is negative, print an error and exit with code `2`.
- If a required argument is missing, `argparse` prints its standard usage error and exits with code `2`.

## 5. Functional Requirements

### FR-1 Tail pod logs
- The application shall start the subprocess `oc logs -f --timestamps <pod> -n <namespace> --since=<since>` (plus `-c <container>` when given).
- With `--timestamps`, OpenShift prefixes each line with the RFC 3339 time at which the container wrote it, followed by a space, for example `2026-11-01T14:05:12.345678901Z ERROR something failed`.
- The application shall read the subprocess's standard output line by line as lines arrive, without waiting for the stream to end.
- Lines shall be decoded as UTF-8, replacing invalid bytes instead of raising errors.

### FR-2 Match lines
- The trailing newline and the timestamp prefix shall be removed from each line. The remaining log text shall be tested with `re.search` against the compiled pattern, so the pattern never matches the timestamp.
- A line counts as a match if `re.search` finds a match. Each matching line adds exactly 1 to the count, however many times the pattern occurs in it.

### FR-3 Count per minute
- Each matching line shall be assigned to a minute bucket based on its **`oc logs --timestamps` prefix**, converted to local time and truncated to the minute (`YYYY-MM-DD HH:MM`). Fractional seconds are ignored.
- The parser shall accept a `Z` suffix or a numeric `+HH:MM` / `-HH:MM` offset, with or without fractional seconds. It shall work on Python 3.9, whose `datetime.fromisoformat` does not accept `Z` or nanoseconds.
- If a line has no parsable timestamp prefix, the whole line is matched and the local time at which the line was read is used instead.
- A minute bucket is complete when either:
  - a matching line with a timestamp in a later minute arrives, or
  - the local clock passes the end of that minute plus the grace period (`--grace`, default 2 seconds).
- **Late lines:** a matching line whose minute has already been reported shall be counted in the current minute, because a reported minute cannot be changed. The number of late lines shall be counted and shown in the summary (FR-6).
- Because minutes are also closed by the local clock, a local clock that is ahead of the cluster's clock by more than the grace period causes late lines. Users can increase `--grace` to allow for this.

### FR-4 Continuous output
- When a minute bucket completes, the application shall print one line to standard output for that minute, then flush stdout immediately so the output works when piped or redirected.
- Output shall happen on minute boundaries even when no log lines arrive. For example, a timer or a read timeout ensures the application does not wait for the next log line before reporting.
- Minutes with zero matching lines shall be printed with a count of `0`. There shall be no gaps in the minute sequence.
- The first reported minute is the minute in which the application started. Because it is a partial minute, it shall be marked `(partial)`.

### FR-5 Output format
Each per-minute result line shall have this format:

```
YYYY-MM-DD HH:MM : <count> lines/min
```

Example:

```
2026-10-30 14:05 : 312 lines/min (partial)
2026-10-30 14:06 : 1180 lines/min
2026-10-30 14:07 : 0 lines/min
2026-10-30 14:08 : 954 lines/min
```

Before the first result, the application shall print one header line to **stderr** describing what it is following, for example:

```
Following pod load-generator-69875cb4c8-jrp8f in namespace load-generator, pattern: "ERROR" (Ctrl+C to stop)
```

Informational and error messages go to stderr so that stdout contains only result lines.

### FR-6 Keyboard interrupt
- On Ctrl+C (`SIGINT` / `KeyboardInterrupt`), the application shall:
  1. Print the count for the current, incomplete minute, marked `(partial)`.
  2. Print a summary line to stderr with the total number of matching lines and the elapsed run time. If any lines were late (FR-3), the summary also gives the number of late lines.
  3. Terminate the `oc` subprocess (send terminate, wait up to 5 seconds, then kill if it is still running).
  4. Exit with code `0`.
- No Python traceback shall be shown on Ctrl+C.

### FR-7 Subprocess failure handling
- If `oc` is not found on `PATH`, print an error to stderr and exit with code `1`.
- If the `oc logs` process exits on its own (for example the pod does not exist, the pod was deleted or restarted, or access was denied), the application shall:
  1. Print the count for the current minute, marked `(partial)`.
  2. Print `oc`'s stderr output and its exit code to stderr.
  3. Exit with code `1`.

## 6. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | **Throughput:** handle at least 10,000 log lines per second without falling behind on a typical workstation. |
| NFR-2 | **Memory:** memory use shall stay constant over time. Lines shall not be stored after they are counted, and only the current minute's counter is kept. |
| NFR-3 | **Timeliness:** with the default grace period, each minute's result shall be printed within 3 seconds after the minute ends (the 2-second grace period plus up to 1 second of processing). |
| NFR-4 | **Thread safety:** if a separate thread is used for reading or timing, the shared counter shall be protected by a lock, or ownership shall be passed through a `queue.Queue`. |
| NFR-5 | **Portability:** the code shall use only the standard library and run unchanged on Linux and macOS. |

## 7. Code Structure

- A single file: `container_log_counter.py`, executable with a `#!/usr/bin/env python3` shebang.
- A `main()` entry point guarded by `if __name__ == "__main__":`, returning the exit code via `sys.exit(main())`.
- Recommended separation of concerns:
  - `parse_args()` — argument parsing and validation.
  - `build_oc_command(args) -> list[str]` — builds the `oc` argument list (never use `shell=True`).
  - `parse_timestamp(line) -> tuple[datetime | None, str]` — splits a line into its local-time timestamp and log text.
  - `MinuteCounter` — a class that holds the current minute and count, and has methods to record a match and to emit completed minutes given the current time. Taking the current time as a parameter keeps it testable without real clocks.
  - A run loop that connects the subprocess, the matcher, the counter, and the output.
- Module and function docstrings and type hints throughout.

## 8. Testing Requirements

Unit tests using `unittest` or `pytest`, without needing a cluster:

1. `build_oc_command` produces the correct arguments, including `--timestamps`, with and without `--container`.
2. An invalid regex causes exit code `2`.
3. `MinuteCounter` emits zero-count minutes for gaps (for example, matches at 10:00 and 10:03 produce output for 10:00, 10:01, and 10:02).
4. `MinuteCounter` counts a line with several pattern occurrences once.
5. Case-insensitive matching works with `--ignore-case`.
6. A run against a fake `oc` (a script that prints timestamped lines and then exits) produces the expected counts and exit code `1`.
7. `parse_timestamp` converts `Z` and numeric-offset timestamps to local time, and returns `(None, line)` for lines without a valid prefix.
8. The pattern is not matched against the timestamp prefix.
9. A matching line whose minute was already reported is counted in the current minute and in the late count.

## 9. Acceptance Criteria

The application is accepted when:

- [ ] `container_log_counter.py -p <pod> -n <ns> -e '<pattern>'` follows the pod's logs and prints one count line per minute.
- [ ] Lines are counted in the minute given by their `oc logs --timestamps` prefix, shown in local time.
- [ ] Minutes with no matching lines are printed with a count of `0`.
- [ ] Output appears in real time when piped, for example `| tee counts.txt`.
- [ ] Ctrl+C prints the partial minute and a summary, then exits cleanly with code `0` and no traceback.
- [ ] No `oc` process is left running after the application exits.
- [ ] All unit tests in Section 8 pass.
