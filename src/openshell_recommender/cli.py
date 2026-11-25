"""`asago-openshell-recommender`: recommend a policy, test it, and run the local OpenShell lab and demo.

`recommend` writes output/<run-id>/policy.yaml (and proves it); `test` deploys and probes that
policy in the lab and writes its evidence into the same run directory.

Paths resolve from the repository root (two levels above this file), so every command
works from any directory. Lab services run in the background: logs go to lab/logs/<name>.log,
PIDs to lab/run/<name>.pid. `lab down` only signals process groups recorded in those PID files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import secrets
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PKG = "openshell_recommender"
LAB = ROOT / "lab"
AGENT = LAB / "agent"
LAB_RUN = LAB / "run"
LAB_LOGS = LAB / "logs"
OUT_LOGS = ROOT / "out" / "logs"
TOKEN_FILE = LAB_RUN / "tool-token"
DEMO = ROOT / "demo"
OUTPUT = ROOT / "output"
EXAMPLE = ROOT / "examples" / "klarna-support"
RUN4 = EXAMPLE / "asago-run4"
PIPELINE_DEFAULT = RUN4
STANDIN = EXAMPLE / "standin-fs-read"
CONDITIONS = RUN4 / "replay" / "conditions"
DEFAULT_MANIFEST = EXAMPLE / "inputs" / "sandbox-manifest-refunds.yaml"
VARIANT = "refunds"
PLANS_STANDIN = STANDIN / "probes.json"
SANDBOX_IMAGE = "localhost/mini-agents-sandbox:asago"
LAB_IMAGE = "localhost/mini-agents-sandbox:lab"
PROBE_SANDBOX = "probe-refunds"  # must be one of probe.ALLOWED_SANDBOXES
GATEWAY_INSTALL = "brew install nvidia/openshell/openshell"
GATEWAY_BIN_REL = Path("libexec") / "openshell-gateway-homebrew-service"
EVAL_CONDITIONS = ("no-sandbox", "recommended", "openshell-default")
RUN_FILES = ("policy.yaml", "provider-profile.yaml", "runtime.json")

# name -> (description, ports). Start order matters: the gateway first.
SERVICES = {
    "gateway": ("OpenShell gateway (podman driver)", (17670,)),
    "mock": ("mock merchant API + attacker sink", (9101, 9102)),
    "tools": ("Klarna MCP tool server", (8765,)),
}
STARTUP_TIMEOUT_S = 180


class CliError(Exception):
    """Expected failure: printed as one line, exit status 1."""


@dataclass
class Row:
    label: str
    ok: bool
    detail: str
    hint: str = ""
    required: bool = True


# ---------------------------------------------------------------- helpers

def run(argv: list[str], *, timeout: float = 60, env: dict | None = None,
        cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, env=env, cwd=cwd)
    except FileNotFoundError:
        raise CliError(f"{argv[0]} not found on PATH") from None


def module_env() -> dict:
    """Environment for `python -m openshell_recommender.X` children, so they import this checkout."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))
    return env


def tool_version(path: str, args: tuple[str, ...] = ("--version",)) -> str:
    try:
        r = run([path, *args], timeout=15)
    except (CliError, subprocess.TimeoutExpired):
        return "version unknown"
    lines = (r.stdout or r.stderr).strip().splitlines()
    return lines[0][:60] if lines else "version unknown"


def podman_machine_state() -> str | None:
    try:
        r = run(["podman", "machine", "inspect", "--format", "{{.State}}"], timeout=30)
    except (CliError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def podman_socket() -> str:
    r = run(["podman", "machine", "inspect", "--format", "{{.ConnectionInfo.PodmanSocket.Path}}"], timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        raise CliError("cannot read the podman machine socket: run `podman machine start`")
    return r.stdout.strip()


def gateway_bin() -> Path:
    """Resolve the OpenShell gateway binary: OPENSHELL_GATEWAY_BIN, then `brew --prefix openshell`."""
    override = os.environ.get("OPENSHELL_GATEWAY_BIN")
    if override:
        p = Path(override).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise CliError(f"OPENSHELL_GATEWAY_BIN={override} is not an executable file")
    brew = shutil.which("brew")
    if brew:
        try:
            r = run([brew, "--prefix", "openshell"], timeout=30)
        except (CliError, subprocess.TimeoutExpired):
            r = None
        if r and r.returncode == 0 and r.stdout.strip():
            p = Path(r.stdout.strip()) / GATEWAY_BIN_REL
            if p.is_file():
                return p
    raise CliError("OpenShell gateway binary not found. Install it with "
                   f"`{GATEWAY_INSTALL}`, or set OPENSHELL_GATEWAY_BIN=/path/to/gateway binary")


def port_listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def port_owner(port: int) -> str:
    try:
        r = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"], timeout=15)
    except (CliError, subprocess.TimeoutExpired):
        return "another process"
    pid = cmd = None
    for line in r.stdout.splitlines():
        if line.startswith("p") and pid is None:
            pid = line[1:]
        elif line.startswith("c") and cmd is None:
            cmd = line[1:]
    return f"{cmd or 'unknown'} pid {pid}" if pid else "another process"


def read_token() -> str:
    try:
        value = TOKEN_FILE.read_text().strip()
    except OSError:
        value = ""
    if not value:
        raise CliError("tool token missing: run `asago-openshell-recommender lab setup`")
    return value


def pid_path(name: str) -> Path:
    return LAB_RUN / f"{name}.pid"


def read_pid(name: str) -> int | None:
    try:
        return int(pid_path(name).read_text().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """True for a running process; a zombie (exited, not yet reaped) counts as gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                               timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return True
    return bool(state) and not state.startswith("Z")


def managed_pid(name: str) -> int | None:
    """PID of a live service this CLI started (a session/process-group leader that owns its PID file)."""
    pid = read_pid(name)
    if pid is None or not pid_alive(pid):
        return None
    try:
        return pid if os.getpgid(pid) == pid else None
    except ProcessLookupError:
        return None


def step(title: str, argv: list[str], log: Path, *, env: dict | None = None, ok_codes=(0,), fatal=True) -> int:
    """Run one pipeline step with its output in `log`; print a one-line result and the log tail on failure."""
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"-> {title}", flush=True)
    t0 = time.time()
    with log.open("w") as fh:
        rc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                            env=env or module_env(), cwd=ROOT).returncode
    took = time.time() - t0
    if rc in ok_codes:
        print(f"   ok ({took:.0f}s), log: {log}")
        return rc
    tail = "\n".join("   | " + ln[:160] for ln in log.read_text(errors="replace").splitlines()[-12:])
    print(f"   failed (rc={rc}), log: {log}\n{tail}")
    if fatal:
        raise CliError(f"{title} failed")
    return rc


def ensure_token() -> str:
    if TOKEN_FILE.exists():
        return "kept (exists)"
    LAB_RUN.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secrets.token_urlsafe(32) + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    return "created (chmod 600)"


# ---------------------------------------------------------------- doctor

def gateway_connected() -> tuple[bool, str]:
    if not shutil.which("openshell"):
        return False, "openshell not installed"
    try:
        r = run(["openshell", "status"], timeout=15)
    except subprocess.TimeoutExpired:
        return False, "status timed out"
    ok = r.returncode == 0 and "Connected" in r.stdout
    return ok, "connected" if ok else "not reachable"


def image_present(name: str) -> bool:
    try:
        return run(["podman", "image", "exists", name], timeout=30).returncode == 0
    except (CliError, subprocess.TimeoutExpired):
        return False


def doctor_rows() -> list[Row]:
    rows: list[Row] = []
    podman = shutil.which("podman")
    rows.append(Row("podman", bool(podman), tool_version(podman) if podman else "not found",
                    "brew install podman"))
    if podman and sys.platform == "darwin":
        state = podman_machine_state()
        rows.append(Row("podman machine", state == "running", state or "no machine",
                        "podman machine init && podman machine start"))
    prover = shutil.which("openshell-prover")
    rows.append(Row("openshell", bool(shutil.which("openshell")),
                    tool_version(shutil.which("openshell") or "openshell") if shutil.which("openshell") else "not found",
                    GATEWAY_INSTALL))
    rows.append(Row("openshell-prover", bool(prover), tool_version(prover) if prover else "not found",
                    GATEWAY_INSTALL + " (ships openshell-prover)"))
    uv = shutil.which("uv")
    rows.append(Row("uv", bool(uv), tool_version(uv) if uv else "not found",
                    "brew install uv  (or: curl -LsSf https://astral.sh/uv/install.sh | sh)"))
    try:
        rows.append(Row("gateway binary", True, str(gateway_bin())))
    except CliError as exc:
        rows.append(Row("gateway binary", False, "not found", str(exc)))

    gw_ok, gw_detail = gateway_connected()
    rows.append(Row("gateway reachable", gw_ok, gw_detail, "asago-openshell-recommender lab up", required=False))
    for img in (SANDBOX_IMAGE, LAB_IMAGE):
        ok = image_present(img)
        rows.append(Row(f"image {img.split('/')[-1]}", ok, "present" if ok else "missing",
                        "asago-openshell-recommender lab setup", required=False))
    if TOKEN_FILE.exists():
        mode = stat.S_IMODE(TOKEN_FILE.stat().st_mode)
        rows.append(Row("tool token", mode & 0o077 == 0, f"present, mode {oct(mode)}",
                        f"chmod 600 {TOKEN_FILE}", required=False))
    else:
        rows.append(Row("tool token", False, "missing", "asago-openshell-recommender lab setup", required=False))
    for svc, (desc, ports) in SERVICES.items():
        for port in ports:
            label = f"port {port} ({svc})"
            if managed_pid(svc):
                rows.append(Row(label, True, f"started by this CLI, pid {managed_pid(svc)}", required=False))
            elif port_listening(port):
                rows.append(Row(label, False, f"in use by {port_owner(port)}",
                                "not started by this CLI: stop it before `asago-openshell-recommender lab up`",
                                required=False))
            else:
                rows.append(Row(label, True, "free", required=False))
    return rows


def cmd_doctor(_a) -> int:
    rows = doctor_rows()
    width = max(len(r.label) for r in rows)
    print("Prerequisites (required)")
    for r in [r for r in rows if r.required]:
        print(f"  {'✓' if r.ok else '✗'} {r.label:<{width}}  {r.detail}")
        if not r.ok and r.hint:
            print(f"      install/fix: {r.hint}")
    print("Lab state (created by lab setup / lab up; not required for recommend)")
    for r in [r for r in rows if not r.required]:
        print(f"  {'✓' if r.ok else '✗'} {r.label:<{width}}  {r.detail}")
        if not r.ok and r.hint:
            print(f"      next: {r.hint}")
    missing = [r for r in rows if r.required and not r.ok]
    if missing:
        print(f"\n{len(missing)} required item(s) missing.")
        return 1
    print("\nAll required prerequisites are present.")
    return 0


# ---------------------------------------------------------------- lab

def build_plan() -> list[tuple[str, list[str]]]:
    podman = shutil.which("podman") or "podman"
    return [
        ("sandbox", [podman, "build", "-f", str(AGENT / "Dockerfile.sandbox"), "-t", SANDBOX_IMAGE, str(AGENT)]),
        ("lab", [podman, "build", "-f", str(LAB / "lab-image" / "Dockerfile"), "-t", LAB_IMAGE,
                 str(LAB / "lab-image")]),
    ]


def cmd_lab_setup(a) -> int:
    builds = build_plan()
    if a.dry_run:
        state = "present (kept)" if TOKEN_FILE.exists() else "would create (random, chmod 600)"
        print(f"tool token {TOKEN_FILE}: {state}")
        for name, argv in builds:
            print(f"build {name} image: {shlex.join(argv)}")
        print("dry run: nothing created or built")
        return 0
    if not shutil.which("podman"):
        raise CliError("podman not found: brew install podman")
    print(f"tool token: {ensure_token()}")
    for name, argv in builds:
        step(f"build {name} image", argv, LAB_LOGS / f"build-{name}.log", env=dict(os.environ))
    print(f"images: {SANDBOX_IMAGE}, {LAB_IMAGE}")
    return 0


def launch_spec(name: str) -> tuple[list[str], Path, dict]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # this CLI's venv must not leak into lab/agent's uv project
    uv = shutil.which("uv") or "uv"
    if name == "gateway":
        if sys.platform != "darwin":
            raise CliError("lab gateway uses the podman machine and is only wired for macOS")
        env.update(OPENSHELL_COMPUTE_DRIVER="podman", OPENSHELL_PODMAN_SOCKET=podman_socket(),
                   OPENSHELL_BIND_ADDRESS="127.0.0.1")
        try:  # as lab/gateway.sh does; a failure here does not stop the gateway from starting
            run(["openshell", "gateway", "select", "openshell"], timeout=30)
        except (CliError, subprocess.TimeoutExpired):
            pass
        return [str(gateway_bin())], LAB, env
    if name == "mock":
        argv = [uv, "run", "python", "-m", "mini_agents.mock_services", "--host", "127.0.0.1",
                "--merchant-port", "9101", "--attacker-port", "9102", "--ledger", str(LAB_RUN / "ledger.jsonl")]
        return argv, AGENT, env
    if name == "tools":
        env["MINI_AGENTS_TOOL_TOKEN"] = read_token()
        argv = [uv, "run", "python", "-m", "mini_agents", "--domain", "klarna", "--transport", "streamable-http",
                "--host", "127.0.0.1", "--port", "8765"]
        return argv, AGENT, env
    raise CliError(f"unknown service {name}")


def stop_service(name: str) -> str:
    pid = read_pid(name)
    if pid is None:
        return "not started by this CLI"
    if not pid_alive(pid):
        pid_path(name).unlink(missing_ok=True)
        return "stale PID file removed"
    try:
        if os.getpgid(pid) != pid:
            return f"pid {pid} is not a process group started by this CLI; left alone"
    except ProcessLookupError:
        pid_path(name).unlink(missing_ok=True)
        return "stale PID file removed"
    def signal_group(sig: int) -> None:
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    signal_group(signal.SIGTERM)
    deadline = time.time() + 10
    while pid_alive(pid) and time.time() < deadline:
        time.sleep(0.3)
    if pid_alive(pid):
        signal_group(signal.SIGKILL)
    pid_path(name).unlink(missing_ok=True)
    return "stopped"


def cmd_lab_up(_a) -> int:
    busy = [f"{svc} (pid {managed_pid(svc)})" for svc in SERVICES if managed_pid(svc)]
    if busy:
        raise CliError("already running from this CLI: " + ", ".join(busy) + " (see `asago-openshell-recommender lab status`)")
    for svc, (_, ports) in SERVICES.items():
        for port in ports:
            if port_listening(port):
                raise CliError(f"port {port} is already in use by {port_owner(port)}; "
                               f"refusing to start {svc}")
    state = podman_machine_state() if sys.platform == "darwin" else "running"
    if state != "running":
        raise CliError("podman machine is not running: run `podman machine start`")
    read_token()  # fail early with the setup hint

    LAB_RUN.mkdir(parents=True, exist_ok=True)
    LAB_LOGS.mkdir(parents=True, exist_ok=True)
    procs: dict[str, subprocess.Popen] = {}
    try:
        for svc, (desc, _) in SERVICES.items():
            argv, cwd, env = launch_spec(svc)
            with (LAB_LOGS / f"{svc}.log").open("ab") as log:
                log.write(f"\n== {dt.datetime.now().isoformat(timespec='seconds')} start {svc}\n".encode())
                try:
                    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                except OSError as exc:
                    raise CliError(f"cannot start {svc}: {exc}")
            procs[svc] = proc
            pid_path(svc).write_text(f"{proc.pid}\n")
            print(f"-> started {svc} ({desc}), pid {proc.pid}")

        pending = {svc: list(ports) for svc, (_, ports) in SERVICES.items()}
        deadline = time.time() + STARTUP_TIMEOUT_S
        while pending and time.time() < deadline:
            for svc in list(pending):
                if procs[svc].poll() is not None:
                    raise CliError(f"{svc} exited (rc={procs[svc].returncode}); see lab/logs/{svc}.log")
                if all(port_listening(p) for p in pending[svc]):
                    print(f"   ✓ {svc} listening on {', '.join(map(str, pending.pop(svc)))}")
            time.sleep(0.5)
        if pending:
            raise CliError(f"not listening after {STARTUP_TIMEOUT_S}s: {', '.join(pending)}; "
                           "see lab/logs/<name>.log (first run of lab/agent can take a while)")
    except CliError:
        for svc in procs:
            stop_service(svc)
        raise
    print("lab is up. `asago-openshell-recommender lab status` to check, `asago-openshell-recommender lab down` to stop.")
    return 0


def cmd_lab_down(_a) -> int:
    for svc in reversed(list(SERVICES)):
        print(f"{svc}: {stop_service(svc)}")
    return 0


def cmd_lab_status(_a) -> int:
    print(f"{'service':<9} {'state':<24} ports")
    for svc, (desc, ports) in SERVICES.items():
        pid = managed_pid(svc)
        state = f"running (pid {pid})" if pid else ("stopped" if read_pid(svc) is None else "stale PID file")
        cells = []
        for port in ports:
            if port_listening(port):
                cells.append(f"{port} listening" + ("" if pid else f" (not started by this CLI: {port_owner(port)})"))
            else:
                cells.append(f"{port} free")
        print(f"{svc:<9} {state:<24} " + "; ".join(cells))
    return 0


# ---------------------------------------------------------------- recommend / test

def utc_run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def probe_plan_step(pipeline: Path, run: Path, logs: Path) -> None:
    """Attack-derived test actions: `probe plan` reads the pipeline's tags and artifacts into run/probes.json."""
    step("derive attack probe plan from pipeline results",
         [sys.executable, "-m", f"{PKG}.probe", "plan", "--variant", VARIANT, "--run-root", str(pipeline),
          "--out", str(run / "probes.json")], logs / "plan.log")


def cmd_recommend(a) -> int:
    run = (a.output_dir / (a.run_id or utc_run_id())).resolve()
    logs = run / "logs"
    manifest = a.manifest.resolve()
    step("compile policy", [sys.executable, "-m", f"{PKG}.compile", "--manifest", str(manifest), "--out", str(run)],
         logs / "compile.log")
    shutil.copyfile(manifest, run / "manifest.yaml")  # bundle reads the manifest from the run dir
    probe_plan_step(a.pipeline_results.resolve(), run, logs)
    step("prove policy (openshell-prover)", [sys.executable, "-m", f"{PKG}.prove", "--policy",
                                             str(run / "policy.yaml"), "--probes", str(run / "probes.json"),
                                             str(PLANS_STANDIN), "--out", str(run)],
         logs / "prove.log", ok_codes=(0, 1))  # 1 = some checks failed; prove.md has the detail
    print(f"run:    {run}\npolicy: {run / 'policy.yaml'}\nprove:  {run / 'prove.md'}")
    print(f"next:   asago-openshell-recommender test --run-dir {run}")
    return 0


def preflight_lab(needs_images: bool) -> None:
    if not TOKEN_FILE.exists():
        raise CliError("tool token missing: run `asago-openshell-recommender lab setup`")
    down = [f"{svc} (port {p})" for svc, (_, ports) in SERVICES.items() for p in ports if not port_listening(p)]
    if down:
        raise CliError("lab is not up: " + ", ".join(down) + ". Run `asago-openshell-recommender lab up`.")
    if needs_images and not image_present(LAB_IMAGE):
        raise CliError(f"image {LAB_IMAGE} missing: run `asago-openshell-recommender lab setup`")


def stage_conditions(run: Path, conditions: list[str]) -> Path:
    """Condition dirs for `probe run --conditions-root`. `recommended` is the run's own policy, not the example's."""
    root = run / "conditions"
    if "recommended" in conditions:
        dst = root / VARIANT / "recommended"
        dst.mkdir(parents=True, exist_ok=True)
        for f in RUN_FILES:
            if (run / f).exists():
                shutil.copyfile(run / f, dst / f)
    if "openshell-default" in conditions:
        shutil.copytree(CONDITIONS / VARIANT / "openshell-default", root / VARIANT / "openshell-default",
                        dirs_exist_ok=True)
    return root


def cmd_test(a) -> int:
    run_dir = a.run_dir.resolve()
    if not (run_dir / "policy.yaml").is_file():
        raise CliError(f"{run_dir / 'policy.yaml'} not found: run `asago-openshell-recommender recommend` first")
    preflight_lab(needs_images=True)
    pipeline = a.pipeline_results.resolve()
    probe_root = run_dir / "probe"
    logs = run_dir / "logs"
    conditions_root = stage_conditions(run_dir, a.conditions)
    plan_file = run_dir / "probes.json"
    probe_plan_step(pipeline, run_dir, logs)
    for cond in a.conditions:
        if cond == "no-sandbox":
            continue
        step(f"deploy + probe {VARIANT}/{cond}",
             [sys.executable, "-m", f"{PKG}.probe", "run", "--variant", VARIANT, "--condition", cond,
              "--conditions-root", str(conditions_root), "--sandbox", PROBE_SANDBOX, "--probes", str(plan_file),
              "--out", str(probe_root), "--deploy"], logs / f"probe-{cond}.log")
    if not a.no_cleanup:
        run(["openshell", "sandbox", "delete", PROBE_SANDBOX], timeout=120)  # best effort, our sandbox only
    if "no-sandbox" in a.conditions:
        step(f"probe {VARIANT}/no-sandbox (plain container)",
             [sys.executable, "-m", f"{PKG}.nosandbox", "--probes", str(plan_file), "--variant", VARIANT,
              "--out", str(probe_root)], logs / "probe-no-sandbox.log")
    step("summarise probe results", [sys.executable, "-m", f"{PKG}.probe", "report", "--out", str(probe_root)],
         logs / "report.log", fatal=False)
    if set(EVAL_CONDITIONS) - set(a.conditions):
        print("bundle skipped: it needs all of " + ", ".join(EVAL_CONDITIONS))
        return 0
    tags = pipeline / "recommender" / "run4-only" / VARIANT / "tags.json"
    step("write validation evidence", [sys.executable, "-m", f"{PKG}.bundle", "--manifest",
                                       str(run_dir / "manifest.yaml"),
                                       "--policy-dir", str(conditions_root / VARIANT / "recommended"),
                                       "--tags", str(tags), "--plans", str(plan_file), str(PLANS_STANDIN),
                                       "--results", str(probe_root), str(STANDIN / "full"),
                                       "--prove", str(run_dir), "--out", str(run_dir)], logs / "bundle.log")
    print(f"results:  {probe_root}\nevidence: {run_dir} (README.md, validation.md, tests.json, gaps.json, "
          "policy-diff.md)")
    return 0


# ---------------------------------------------------------------- demo

def cmd_demo(a) -> int:
    if a.demo_cmd == "build":
        step("build demo data", ["bash", str(ROOT / "scripts" / "build-demo.sh")], OUT_LOGS / "demo-build.log",
             env=dict(module_env(), PYTHON=sys.executable))
        print(f"demo data: {DEMO / 'data'}")
        return 0
    if a.demo_cmd == "check":
        log = OUT_LOGS / "demo-check.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        print("-> check demo in headless Chrome (needs Chrome; CHROME=/path to override)", flush=True)
        with log.open("w") as fh:
            rc = subprocess.run(["bash", str(ROOT / "scripts" / "check-demo.sh")], stdin=subprocess.DEVNULL,
                                stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT,
                                env=dict(module_env(), PYTHON=sys.executable)).returncode
        lines = log.read_text(errors="replace").strip().splitlines()
        print(f"result: {lines[-1] if lines else 'no output'} (rc={rc}), log: {log}")
        return rc
    if port_listening(a.port):
        raise CliError(f"port {a.port} is already in use by {port_owner(a.port)}")
    print(f"serving {DEMO} at http://127.0.0.1:{a.port}/ (Ctrl-C to stop)")
    try:
        subprocess.call([sys.executable, "-m", "http.server", str(a.port), "--bind", "127.0.0.1",
                         "--directory", str(DEMO)])
    except KeyboardInterrupt:
        pass
    return 0


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="asago-openshell-recommender",
                                 description="OpenShell sandbox-policy recommender, test harness and local lab.")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    sub.add_parser("doctor", help="check prerequisites and lab state").set_defaults(func=cmd_doctor)

    lab = sub.add_parser("lab", help="local OpenShell lab (setup, up, down, status)")
    lab_sub = lab.add_subparsers(dest="lab_cmd", required=True, metavar="ACTION")
    setup = lab_sub.add_parser("setup", help="create the tool token and build the two images")
    setup.add_argument("--dry-run", action="store_true", help="print the plan; create and build nothing")
    setup.set_defaults(func=cmd_lab_setup)
    lab_sub.add_parser("up", help="start the gateway, mock services and tool server in the background").set_defaults(
        func=cmd_lab_up)
    lab_sub.add_parser("down", help="stop the services this CLI started").set_defaults(func=cmd_lab_down)
    lab_sub.add_parser("status", help="show each service and its port").set_defaults(func=cmd_lab_status)

    rec = sub.add_parser("recommend", help="compile a policy from a manifest and prove it (writes policy.yaml)")
    rec.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                     help="sandbox manifest (default: Klarna support refunds)")
    rec.add_argument("--pipeline-results", type=Path, default=PIPELINE_DEFAULT,
                     help="ASAGO pipeline results dir; its tags and artifacts give the test actions "
                          "(default: examples/klarna-support/asago-run4)")
    rec.add_argument("--run-id", help="run ID (default: UTC timestamp, e.g. 20261125-143000)")
    rec.add_argument("--output-dir", type=Path, default=OUTPUT,
                     help="output root; the run is written to <output-dir>/<run-id>/ (default: output/)")
    rec.set_defaults(func=cmd_recommend)

    test = sub.add_parser("test", help="deploy and probe the run's policy and conditions, then write validation "
                                       "evidence (needs `lab up`)")
    test.add_argument("--run-dir", type=Path, required=True,
                      help="run directory written by recommend (output/<run-id>)")
    test.add_argument("--pipeline-results", type=Path, default=PIPELINE_DEFAULT,
                      help="ASAGO pipeline results dir for the test actions (default: examples/klarna-support/asago-run4)")
    test.add_argument("--conditions", nargs="+", choices=EVAL_CONDITIONS, default=list(EVAL_CONDITIONS),
                      help="conditions to measure: recommended is the run's policy.yaml (default: all three)")
    test.add_argument("--no-cleanup", action="store_true", help="keep the probe sandbox after the run")
    test.set_defaults(func=cmd_test)

    demo = sub.add_parser("demo", help="build, check or serve the static demo site")
    demo_sub = demo.add_subparsers(dest="demo_cmd", required=True, metavar="ACTION")
    demo_sub.add_parser("build", help="regenerate demo/data from the run4 outputs").set_defaults(func=cmd_demo)
    demo_sub.add_parser("check", help="click through the demo in headless Chrome").set_defaults(func=cmd_demo)
    serve = demo_sub.add_parser("serve", help="serve demo/ on 127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(func=cmd_demo)

    return ap


def main(argv: list[str] | None = None) -> int:
    if not (ROOT / "pyproject.toml").is_file():
        print(f"asago-openshell-recommender: repository root not found at {ROOT} "
              "(is the package installed non-editable?)", file=sys.stderr)
        return 1
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except CliError as exc:
        print(f"asago-openshell-recommender: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
