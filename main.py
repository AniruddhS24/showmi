#!/usr/bin/env python3
"""Showmi CLI — self-learning browser agent."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _data_dir():
    from db import SHOWMI_DIR, LOGS_DIR
    SHOWMI_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return SHOWMI_DIR, LOGS_DIR


def _pid_file():
    d, _ = _data_dir()
    return d / "showmi.pid"


def _log_file():
    _, logs = _data_dir()
    return logs / "server.log"


def _source_dir():
    """Find the source directory (where server.py lives)."""
    return Path(__file__).resolve().parent


def _read_pid():
    """Read PID from file and verify the process is alive. Returns PID or None."""
    pf = _pid_file()
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)  # check if alive
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        pf.unlink(missing_ok=True)
        return None


def _check_port(port):
    """Check if a port is already in use."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_health(port, timeout=8):
    """Wait for the server health endpoint to respond."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return json.loads(req.read()).get("status") == "ok"
        except Exception:
            time.sleep(0.3)
    return False


# ── Commands ──

def cmd_start(args):
    """Start the server as a background daemon."""
    from db import init_db
    init_db()

    port = args.port
    pid = _read_pid()
    if pid:
        print(f"Showmi is already running (PID: {pid})")
        return

    if _check_port(port):
        print(f"Port {port} is already in use. Use -p to pick another port.")
        return

    src = _source_dir()
    log = _log_file()
    log_fh = open(log, "a")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(src),
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )

    _pid_file().write_text(str(proc.pid))

    print(f"Starting Showmi on :{port}...", end=" ", flush=True)
    if _wait_for_health(port):
        print(f"running (PID: {proc.pid})")
        print(f"  Logs: {log}")
    else:
        print("failed to start. Check logs:")
        print(f"  showmi logs")


def cmd_stop(args):
    """Stop the background server."""
    pid = _read_pid()
    if not pid:
        print("Showmi is not running.")
        return

    print(f"Stopping Showmi (PID: {pid})...", end=" ", flush=True)
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait for graceful shutdown
        for _ in range(50):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            # Force kill if still alive
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except ProcessLookupError:
        pass

    _pid_file().unlink(missing_ok=True)
    print("stopped.")


def cmd_restart(args):
    """Restart the server."""
    cmd_stop(args)
    time.sleep(0.5)
    cmd_start(args)


def cmd_serve(args):
    """Start the server in the foreground (for development)."""
    import uvicorn
    from db import init_db

    init_db()
    print(f"Starting Showmi (foreground) on :{args.port}")
    uvicorn.run("server:app", host="0.0.0.0", port=args.port, reload=args.reload)


def cmd_logs(args):
    """Tail the server logs."""
    log = _log_file()
    if not log.exists():
        print("No logs yet. Start the server first: showmi start")
        return
    try:
        subprocess.run(["tail", "-n", str(args.lines), "-f", str(log)])
    except KeyboardInterrupt:
        pass


def cmd_run(args):
    """Run a one-off browser task."""
    if args.confirm:
        import config
        object.__setattr__(config.config, "require_confirmation", True)

    from agent import run_agent
    asyncio.run(run_agent(args.task))


def cmd_models(args):
    """Manage LLM model configurations."""
    from db import init_db, list_models, save_model, delete_model, set_active_model, get_active_model

    init_db()
    action = args.action

    if action == "list":
        models = list_models()
        if not models:
            print("No models configured. Add one with: showmi models add")
            return
        active = get_active_model()
        active_id = active["id"] if active else None
        for m in models:
            marker = " *" if m["id"] == active_id else "  "
            key_preview = ("..." + m["api_key"][-4:]) if len(m.get("api_key", "")) > 4 else "****"
            print(f"{marker} {m['name']:<20} {m['provider']:<12} {m['model']:<30} {key_preview}")
        print(f"\n  (* = active)")

    elif action == "add":
        name = args.name or _prompt("Name: ")
        provider = args.provider or _prompt("Provider (anthropic/openai/local): ", default="anthropic")
        model = args.model or _prompt("Model ID: ")
        api_key = args.api_key or _prompt("API Key: ")
        base_url = args.base_url or _prompt("Base URL (blank for default): ", default="")
        temperature = args.temperature

        result = save_model({
            "name": name,
            "provider": provider,
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "temperature": temperature,
        })
        models = list_models()
        if len(models) == 1:
            set_active_model(result["id"])
        print(f"Saved model: {name} ({result['id']})")

    elif action == "rm":
        if not args.name:
            print("Usage: showmi models rm <name>")
            return
        models = list_models()
        target = next((m for m in models if m["name"] == args.name or m["id"] == args.name), None)
        if not target:
            print(f"Model not found: {args.name}")
            return
        delete_model(target["id"])
        print(f"Deleted: {target['name']}")

    elif action == "activate":
        if not args.name:
            print("Usage: showmi models activate <name>")
            return
        models = list_models()
        target = next((m for m in models if m["name"] == args.name or m["id"] == args.name), None)
        if not target:
            print(f"Model not found: {args.name}")
            return
        set_active_model(target["id"])
        print(f"Activated: {target['name']}")

    else:
        print(f"Unknown action: {action}. Use: list, add, rm, activate")


def cmd_sessions(args):
    """List chat sessions."""
    from db import init_db, list_sessions, get_session_messages

    init_db()

    if args.session_id:
        messages = get_session_messages(args.session_id)
        if not messages:
            print(f"No messages found for session: {args.session_id}")
            return
        for msg in messages:
            role = msg["role"].upper()
            content = msg["content"] or ""
            if len(content) > 200:
                content = content[:200] + "..."
            print(f"  [{role}] {content}")
        return

    sessions = list_sessions(limit=args.limit)
    if not sessions:
        print("No sessions yet.")
        return
    for s in sessions:
        title = s["title"] or "untitled"
        if len(title) > 60:
            title = title[:60] + "..."
        print(f"  {s['id'][:8]}  {s['created_at'][:16]}  {title}")


def cmd_status(args):
    """Check server and config status."""
    from db import init_db, list_models, get_active_model, SHOWMI_DIR, DB_PATH

    init_db()

    print(f"Data dir:  {SHOWMI_DIR}")
    print(f"Database:  {DB_PATH} ({'exists' if DB_PATH.exists() else 'missing'})")

    models = list_models()
    active = get_active_model()
    print(f"Models:    {len(models)} configured")
    if active:
        print(f"Active:    {active['name']} ({active['provider']}/{active['model']})")
    else:
        print("Active:    none")

    pid = _read_pid()
    port = args.port
    if pid:
        print(f"Server:    running (PID: {pid}, port: {port})")
    elif _check_port(port):
        print(f"Server:    something is using port {port} (not managed by showmi)")
    else:
        print(f"Server:    not running")


def cmd_upgrade(args):
    """Pull latest code, reinstall dependencies, and restart server if running."""
    import shutil

    repo_dir = SHOWMI_HOME / "repo"
    venv_dir = SHOWMI_HOME / ".venv"

    if not repo_dir.exists():
        print("Showmi repo not found. Run the install script first.")
        return

    # Pull latest
    print("Pulling latest...", end=" ", flush=True)
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "pull", "--ff-only"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("failed.")
        print(result.stderr.strip())
        return

    output = result.stdout.strip()
    if "Already up to date" in output:
        print("already up to date.")
    else:
        print("done.")
        # Show what changed
        for line in output.splitlines():
            if line.strip():
                print(f"  {line.strip()}")

    # Reinstall dependencies
    print("Installing dependencies...", end=" ", flush=True)
    if shutil.which("uv"):
        subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_dir / "bin" / "python"),
             "-e", str(repo_dir), "--quiet"],
            capture_output=True,
        )
    else:
        subprocess.run(
            [str(venv_dir / "bin" / "pip"), "install", "-e", str(repo_dir), "--quiet"],
            capture_output=True,
        )
    print("done.")

    # Restart server if it was running
    pid = _read_pid()
    if pid:
        print("Restarting server...", end=" ", flush=True)
        cmd_stop(args)
        time.sleep(0.5)
        cmd_start(args)
    else:
        print("Server not running (use 'showmi start' to start).")

    print("\nUpgrade complete!")
    print(f"  Extension: {repo_dir / 'extension'}")
    print("  Reload the extension in chrome://extensions to pick up changes.")


SHOWMI_HOME = Path.home() / ".showmi"
LINK_PATH = Path.home() / ".local" / "bin" / "showmi"


def cmd_uninstall(args):
    """Stop the server and remove all Showmi files."""
    import shutil

    # Stop server first
    pid = _read_pid()
    if pid:
        cmd_stop(args)

    confirm = input("This will delete ~/.showmi and all data. Continue? [y/N] ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Aborted.")
        return

    # Remove symlink
    if LINK_PATH.is_symlink() or LINK_PATH.exists():
        LINK_PATH.unlink()
        print(f"  Removed {LINK_PATH}")

    # Remove ~/.showmi
    if SHOWMI_HOME.exists():
        shutil.rmtree(SHOWMI_HOME)
        print(f"  Removed {SHOWMI_HOME}")

    print("\nShowmi uninstalled.")


def _prompt(text, default=None):
    if default:
        text = f"{text}[{default}] "
    val = input(text).strip()
    return val if val else (default or "")


def cli():
    parser = argparse.ArgumentParser(
        prog="showmi",
        description="Showmi — self-learning browser agent",
    )
    sub = parser.add_subparsers(dest="command")

    # start (daemon)
    p_start = sub.add_parser("start", help="Start the server (background)")
    p_start.add_argument("-p", "--port", type=int, default=8765)

    # stop
    sub.add_parser("stop", help="Stop the background server")

    # restart
    p_restart = sub.add_parser("restart", help="Restart the server")
    p_restart.add_argument("-p", "--port", type=int, default=8765)

    # serve (foreground, dev)
    p_serve = sub.add_parser("serve", help="Start in foreground (dev mode)")
    p_serve.add_argument("-p", "--port", type=int, default=8765)
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload on changes")

    # logs
    p_logs = sub.add_parser("logs", help="Tail server logs")
    p_logs.add_argument("-n", "--lines", type=int, default=50)

    # run
    p_run = sub.add_parser("run", help="Run a one-off browser task")
    p_run.add_argument("task", help="Task description")
    p_run.add_argument("--confirm", action="store_true")

    # models
    p_models = sub.add_parser("models", help="Manage LLM models")
    p_models.add_argument("action", nargs="?", default="list", help="list, add, rm, activate")
    p_models.add_argument("name", nargs="?", help="Model name or ID")
    p_models.add_argument("--provider")
    p_models.add_argument("--model")
    p_models.add_argument("--api-key")
    p_models.add_argument("--base-url")
    p_models.add_argument("--temperature", type=float, default=0.5)

    # sessions
    p_sessions = sub.add_parser("sessions", help="List chat sessions")
    p_sessions.add_argument("session_id", nargs="?")
    p_sessions.add_argument("-n", "--limit", type=int, default=20)

    # status
    p_status = sub.add_parser("status", help="Check server and config status")
    p_status.add_argument("-p", "--port", type=int, default=8765)

    # upgrade
    p_upgrade = sub.add_parser("upgrade", help="Pull latest code and reinstall")
    p_upgrade.add_argument("-p", "--port", type=int, default=8765)

    # uninstall
    sub.add_parser("uninstall", help="Remove Showmi and all data")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "serve": cmd_serve,
        "logs": cmd_logs,
        "run": cmd_run,
        "models": cmd_models,
        "sessions": cmd_sessions,
        "status": cmd_status,
        "upgrade": cmd_upgrade,
        "uninstall": cmd_uninstall,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    cli()
