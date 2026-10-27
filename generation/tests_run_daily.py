#!/usr/bin/env python3
"""
Shell-level tests for run_daily.sh's weekly registry_refresh step and
fly-deploy/entrypoint.sh's optional LINKEDIN_ENV_B64 handling (both added
2026-10-27 with the registry carry-forward refresh).

Run: `python3 generation/tests_run_daily.py` (from anywhere). Needs `bash`
on PATH (Linux CI, or Git Bash on Windows; RUN_DAILY_TEST_BASH=<path>
overrides) plus coreutils (`timeout`, `base64`, `date`). Nothing real runs:
  - run_daily.sh is executed for real, but GENERATION_PYTHON_BIN points at a
    stub `python` (a bash script) that only appends its argv to a log file
    and exits 0 -- or STUB_FAIL_RC when its argv contains STUB_FAIL_MATCH.
    A stub `date` earlier on PATH pins `date +%u` (the IST weekday) via
    FAKE_IST_WEEKDAY and delegates every other call to the real date.
  - entrypoint.sh is copied with its /app and /tmp paths rewritten into a
    temp dir; its final `exec run_daily.sh` hits a stub that only reports
    whether the LinkedIn .env got materialized. Every secret is a dummy.

Asserts:
  (s) both scripts parse (`bash -n`, one file per call -- `bash -n a b`
      would only check a).
  (d1) a failing registry_refresh is FAIL-SOFT: exit 0, and generate_assets,
       gate_keywords, export_negatives, instrument, notify_slack all still
       run, after it; the soft failure is reported at the end.
  (d2) step order: collect_kw_perf -> lead_quality -> registry_refresh ->
       generate_assets.
  (d3) --from-linkedin only when ${IK_MCP_DIR}/linkedin-ads/.env exists.
  (d4) --only-if-stale-days $REGISTRY_STALE_DAYS (default 7) on non-Monday
       runs; absent on Monday IST and with FORCE_REGISTRY_REFRESH=true.
  (d5) REGISTRY_REFRESH_ENABLED=false never invokes the builder.
  (d6) REGISTRY_REFRESH_TIMEOUT_S bounds a hung builder (exit 124, still
       fail-soft).
  (d7) every OTHER step is still fail-fast (a failing generate_assets stops
       the chain with its exit code).
  (e1-e4) entrypoint.sh: LINKEDIN_ENV_B64 unset -> Google-only log, no file;
       valid -> file materialized byte-exact and never echoed; undecodable
       -> WARN, no file, container continues; a missing REQUIRED secret
       still exits 2.
"""
import base64
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN_DAILY = REPO / "run_daily.sh"
ENTRYPOINT = REPO / "fly-deploy" / "entrypoint.sh"

CHAIN = ["collect_kw_perf.py", "lead_quality.py", "build_campaign_registry.py",
         "generate_assets.py", "gate_keywords.py", "export_negatives.py",
         "instrument.py", "notify_slack.py"]

STUB_PYTHON = """#!/bin/bash
printf '%s\\n' "$*" >> "$STUB_LOG"
if [ -n "${STUB_SLEEP_MATCH:-}" ]; then
  case "$*" in *"$STUB_SLEEP_MATCH"*) exec sleep "${STUB_SLEEP:-30}" ;; esac
fi
if [ -n "${STUB_FAIL_MATCH:-}" ]; then
  case "$*" in *"$STUB_FAIL_MATCH"*) exit "${STUB_FAIL_RC:-1}" ;; esac
fi
exit 0
"""

STUB_DATE = """#!/bin/bash
if [ "${1:-}" = "+%u" ] && [ -n "${FAKE_IST_WEEKDAY:-}" ]; then
  echo "$FAKE_IST_WEEKDAY"; exit 0
fi
for d in /usr/bin /bin; do
  if [ -x "$d/date" ]; then exec "$d/date" "$@"; fi
done
echo "stub date: no real date binary found" >&2
exit 127
"""

STUB_RUN_DAILY = """#!/bin/bash
if [ -f "$IK_MCP_DIR/linkedin-ads/.env" ]; then li=yes; else li=no; fi
echo "STUB_RUN_DAILY linkedin_env=$li"
exit 0
"""


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def _bash() -> str:
    b = os.environ.get("RUN_DAILY_TEST_BASH") or shutil.which("bash")
    if not b:
        print("FAIL: bash not found on PATH (set RUN_DAILY_TEST_BASH)")
        sys.exit(1)
    return b


def _write_exec(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(path, 0o755)
    return path


def _posix(p: Path) -> str:
    # Forward slashes work for both Linux bash and Git Bash (C:/Users/...).
    return p.as_posix()


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.bin = root / "fakebin"
        self.bin.mkdir()
        _write_exec(self.bin / "date", STUB_DATE)
        self.python = _write_exec(root / "stub_python", STUB_PYTHON)
        self.mcp = root / "mcp"
        self.mcp.mkdir()
        self.log = root / "stub.log"

    def linkedin_env(self, present: bool):
        p = self.mcp / "linkedin-ads" / ".env"
        if present:
            p.parent.mkdir(exist_ok=True)
            p.write_text("LINKEDIN_ACCESS_TOKEN=dummy\n", encoding="utf-8")
        elif p.exists():
            p.unlink()

    def run_daily(self, **env_over) -> tuple[int, str, list[str], float]:
        if self.log.exists():
            self.log.unlink()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("REGISTRY_", "FORCE_", "STUB_", "LEAD_QUALITY_"))}
        env.update({
            "PATH": str(self.bin) + os.pathsep + env.get("PATH", ""),
            "GENERATION_PYTHON_BIN": _posix(self.python),
            "IK_MCP_DIR": _posix(self.mcp),
            "STUB_LOG": _posix(self.log),
            "DRY_RUN": "true",
            "FAKE_IST_WEEKDAY": "3",
            "STUB_FAIL_MATCH": "build_campaign_registry.py",
        })
        env.update(env_over)
        t0 = time.monotonic()
        proc = subprocess.run([_bash(), _posix(RUN_DAILY)], env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180,
                              check=False)
        elapsed = time.monotonic() - t0
        calls = (self.log.read_text(encoding="utf-8").splitlines()
                 if self.log.exists() else [])
        return proc.returncode, proc.stdout + proc.stderr, calls, elapsed


def _index(calls: list[str], script: str) -> int:
    for i, c in enumerate(calls):
        if script in c:
            return i
    return -1


def _builder_args(calls: list[str]) -> str:
    i = _index(calls, "build_campaign_registry.py")
    return calls[i] if i >= 0 else ""


def test_syntax():
    for script in (RUN_DAILY, ENTRYPOINT):
        proc = subprocess.run([_bash(), "-n", _posix(script)], capture_output=True, text=True,
                              check=False)
        check(proc.returncode == 0, f"(s) bash -n {script.name} parses ({proc.stderr.strip()})")


def test_run_daily(sb: Sandbox):
    # (d1) + (d2) + (d3 absent) + (d4 default 7)
    sb.linkedin_env(False)
    rc, out, calls, _ = sb.run_daily()
    check(rc == 0, f"(d1) failing registry_refresh -> run_daily.sh still exits 0 (got {rc})\n{out}")
    order = [_index(calls, s) for s in CHAIN]
    check(all(i >= 0 for i in order) and order == sorted(order),
          f"(d1/d2) every step ran, in order, with generate_assets..notify_slack "
          f"AFTER the failed builder (indices {order})")
    check("[registry_refresh] FAILED (exit=1) -- fail-soft, continuing chain" in out
          and "fail-soft step(s) FAILED: registry_refresh" in out,
          "(d1) the soft failure is logged at the step and in the final COMPLETE line")
    args = _builder_args(calls)
    check("--refresh --from-google" in args and "--from-linkedin" not in args,
          f"(d3) no linkedin-ads/.env -> Google-only refresh (args: {args!r})")
    check("LinkedIn creds absent" in out, "(d3) the Google-only fallback is logged")
    check("--only-if-stale-days 7" in args,
          f"(d4) non-Monday run passes --only-if-stale-days 7 (args: {args!r})")

    # (d3 present) + builder succeeding
    sb.linkedin_env(True)
    rc, out, calls, _ = sb.run_daily(STUB_FAIL_MATCH="")
    args = _builder_args(calls)
    check(rc == 0 and "--from-linkedin" in args,
          f"(d3) linkedin-ads/.env present -> --from-linkedin passed (args: {args!r})")
    check("[registry_refresh] OK" in out and "COMPLETE -- all steps ok" in out,
          "(d3) a successful refresh logs OK and the run completes 'all steps ok'")
    sb.linkedin_env(False)

    # (d4) Monday IST / FORCE -> full refresh; custom stale threshold
    _rc, _out, calls, _ = sb.run_daily(FAKE_IST_WEEKDAY="1")
    check("--only-if-stale-days" not in _builder_args(calls),
          f"(d4) Monday IST -> no --only-if-stale-days ({_builder_args(calls)!r})")
    _rc, _out, calls, _ = sb.run_daily(FORCE_REGISTRY_REFRESH="true")
    check("--only-if-stale-days" not in _builder_args(calls),
          "(d4) FORCE_REGISTRY_REFRESH=true -> no --only-if-stale-days")
    _rc, _out, calls, _ = sb.run_daily(REGISTRY_STALE_DAYS="3")
    check("--only-if-stale-days 3" in _builder_args(calls),
          "(d4) REGISTRY_STALE_DAYS=3 is passed through")

    # (d5) disabled -> builder never invoked, chain unaffected
    rc, out, calls, _ = sb.run_daily(REGISTRY_REFRESH_ENABLED="false")
    check(rc == 0 and _index(calls, "build_campaign_registry.py") == -1
          and _index(calls, "generate_assets.py") >= 0
          and "[registry_refresh] SKIPPED" in out,
          "(d5) REGISTRY_REFRESH_ENABLED=false skips the builder entirely")

    # (d6) a hung builder is killed by the timeout bound, still fail-soft
    rc, out, calls, elapsed = sb.run_daily(
        STUB_FAIL_MATCH="", STUB_SLEEP_MATCH="build_campaign_registry.py", STUB_SLEEP="30",
        REGISTRY_REFRESH_TIMEOUT_S="1")
    check(rc == 0 and "[registry_refresh] FAILED (exit=124)" in out
          and _index(calls, "notify_slack.py") >= 0 and elapsed < 20,
          f"(d6) REGISTRY_REFRESH_TIMEOUT_S=1 kills a hung builder (exit 124) and "
          f"the chain finishes (rc={rc}, {elapsed:.1f}s)")

    # (d7) other steps remain fail-fast
    rc, out, calls, _ = sb.run_daily(STUB_FAIL_MATCH="generate_assets.py", STUB_FAIL_RC="5")
    check(rc == 5 and _index(calls, "gate_keywords.py") == -1
          and _index(calls, "build_campaign_registry.py") >= 0
          and "stopping chain" in out,
          f"(d7) a failing generate_assets still stops the chain with its exit code (rc={rc})")


def _entrypoint_copy(root: Path) -> tuple[Path, Path]:
    app = root / "app"
    app.mkdir()
    _write_exec(app / "run_daily.sh", STUB_RUN_DAILY)
    text = ENTRYPOINT.read_text(encoding="utf-8")
    # "/app" first: the temp paths substituted in are never re-scanned by a
    # later replace. (The rewritten script inlines these paths unquoted, as
    # the original does -- fine for the space-free temp dirs used here.)
    text = text.replace("/app", _posix(app))
    text = text.replace("/tmp/generation_bq_service_account.json",
                        _posix(root / "sa.json"))
    return _write_exec(root / "entrypoint.sh", text), app


def test_entrypoint(root: Path):
    script, app = _entrypoint_copy(root)
    li_env = app / "linkedin-ads" / ".env"
    li_plain = "LINKEDIN_ACCESS_TOKEN=li-secret-value-123\nLINKEDIN_AD_ACCOUNT_ID=42\n"
    li_b64 = base64.b64encode(li_plain.encode()).decode()

    def run(**env_over):
        env = dict(os.environ)
        env.pop("LINKEDIN_ENV_B64", None)
        env.update({
            "GOOGLE_ADS_ENV_B64": base64.b64encode(b"DUMMY=1\n").decode(),
            "SLACK_ENV_B64": base64.b64encode(b"DUMMY=1\n").decode(),
            "GENERATION_BQ_SERVICE_ACCOUNT_B64": base64.b64encode(b"{}").decode(),
            "CLAUDE_CODE_OAUTH_TOKEN": "dummy-not-a-token",
            "TZ": "Asia/Kolkata",
            "HOME": _posix(root / "home"),
        })
        for k, v in env_over.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        if li_env.exists():
            li_env.unlink()
        p = subprocess.run([_bash(), _posix(script)], env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60,
                           check=False)
        return p.returncode, p.stdout + p.stderr

    rc, out = run()
    check(rc == 0 and "STUB_RUN_DAILY linkedin_env=no" in out
          and "LINKEDIN_ENV_B64 not set -- registry refresh will be Google-only" in out,
          f"(e1) LINKEDIN_ENV_B64 unset -> optional, Google-only log, no file (rc={rc})\n{out}")

    rc, out = run(LINKEDIN_ENV_B64=li_b64)
    check(rc == 0 and "STUB_RUN_DAILY linkedin_env=yes"
          in out and li_env.read_text(encoding="utf-8") == li_plain,
          f"(e2) valid LINKEDIN_ENV_B64 -> linkedin-ads/.env materialized byte-exact (rc={rc})")
    check("li-secret-value-123" not in out and li_b64 not in out
          and "LinkedIn creds materialized" in out,
          "(e2) neither the secret value nor its base64 is ever echoed")
    if os.name != "nt":
        check(stat.S_IMODE(li_env.stat().st_mode) == 0o600,
              "(e2) linkedin-ads/.env is chmod 600")

    bad = "%%%not-base64%%%"
    rc, out = run(LINKEDIN_ENV_B64=bad)
    check(rc == 0 and "STUB_RUN_DAILY linkedin_env=no" in out
          and "WARN: LINKEDIN_ENV_B64 failed to decode" in out and not li_env.exists()
          and bad not in out,
          f"(e3) undecodable LINKEDIN_ENV_B64 -> WARN, no file, container still "
          f"hands off to run_daily.sh (rc={rc})\n{out}")

    rc, out = run(SLACK_ENV_B64=None)
    check(rc == 2 and "FATAL: required Fly secret 'SLACK_ENV_B64'" in out
          and "STUB_RUN_DAILY" not in out,
          f"(e4) a missing REQUIRED secret still exits 2 before run_daily (rc={rc})")


def main():
    test_syntax()
    with tempfile.TemporaryDirectory(prefix="tests_run_daily_") as tmp:
        test_run_daily(Sandbox(Path(tmp)))
    with tempfile.TemporaryDirectory(prefix="tests_entrypoint_") as tmp:
        test_entrypoint(Path(tmp))
    print("\nAll tests_run_daily assertions passed.")


if __name__ == "__main__":
    main()
