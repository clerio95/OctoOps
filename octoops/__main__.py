"""Entry point: `python -m octoops [--setup]`.

If config.toml is missing (or --setup is passed) the wizard runs first; otherwise
the config is loaded and the runtime is started. The async serve loop starts the
transports and awaits a shutdown signal (SIGINT/SIGTERM), then shuts down cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from octoops.core.bootstrap import (
    Runtime,
    build_runtime,
    start_runtime,
    stop_runtime,
)
from octoops.core.config import AppConfig
from octoops.core.errors import OctoOpsError
from octoops.core.logging import configure_logging, get_logger
from octoops.core.paths import AppPaths
from octoops.transports import Transport, build_transports

DEFAULT_CONFIG_PATH = "config.toml"


async def _run_transport(name: str, transport: Transport, runtime: Runtime) -> None:
    """Run a transport, guarding against crashes so one can't kill the process."""
    log = get_logger("octoops.main")
    try:
        await transport.run(runtime.router, runtime.registry)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - isolate transport failures
        log.error(
            "transport.crashed",
            transport=name,
            error=str(exc),
            error_type=type(exc).__name__,
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="octoops")
    parser.add_argument(
        "--setup", action="store_true", help="Run the setup wizard, then start."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run setup diagnostics and exit (does not start the bot).",
    )
    parser.add_argument(
        "--verify-token",
        action="store_true",
        help="With --check, validate the Telegram bot token live against the API "
        "(requires network).",
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH, help="Path to config.toml."
    )
    return parser.parse_args(argv)


def _maybe_start_mcp(runtime: Runtime, log) -> "asyncio.Task | None":
    """Start the optional MCP server task if [mcp] enabled and the extra is installed."""
    if not runtime.registry.config.mcp.enabled:
        return None
    try:
        from octoops.mcp.server import serve_mcp
        from octoops.mcp.service import McpService
    except ImportError as exc:
        log.error("mcp.extra_missing", error=str(exc), hint="pip install octoops[mcp]")
        return None

    service = McpService(runtime.registry, runtime.router, runtime.registry.config.mcp)

    async def _guarded() -> None:
        try:
            await serve_mcp(service)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolate MCP failures
            log.error("mcp.crashed", error=str(exc), error_type=type(exc).__name__)

    return asyncio.create_task(_guarded(), name="mcp")


async def _serve(runtime: Runtime) -> None:
    log = get_logger("octoops.main")
    await start_runtime(runtime)

    transports = build_transports(runtime.registry)
    runtime.registry.transports.update(transports)

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        log.info("signal.shutdown_requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows: add_signal_handler is unsupported. Schedule onto the loop
            # via call_soon_threadsafe so the waiting task actually wakes.
            signal.signal(
                sig, lambda *_: loop.call_soon_threadsafe(_request_stop)
            )

    transport_tasks = [
        asyncio.create_task(_run_transport(name, t, runtime), name=f"transport:{name}")
        for name, t in transports.items()
    ]

    mcp_task = _maybe_start_mcp(runtime, log)
    if mcp_task is not None:
        transport_tasks.append(mcp_task)

    log.info("octoops.running", transports=list(transports), mcp=mcp_task is not None)

    try:
        await stop_event.wait()
    finally:
        for task in transport_tasks:
            task.cancel()
        if transport_tasks:
            await asyncio.gather(*transport_tasks, return_exceptions=True)
        await stop_runtime(runtime)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config_path = Path(args.config)
    paths = AppPaths.from_config(config_path)

    if args.check:
        from octoops.core.doctor import run_checks

        return run_checks(config_path, paths, verify_token=args.verify_token)

    # One instance per install. Two would fight over Telegram polling (409
    # conflicts) and the bridge/callback ports; --setup is included so pairing
    # can't spawn a second bridge while the scheduled task instance is alive.
    # The OS releases the lock on process death — no stale-lock cleanup needed.
    from octoops.core.instance_lock import InstanceLock

    lock = InstanceLock(paths.data / "octoops.lock")
    if not lock.acquire():
        holder = lock.holder()
        print(
            f"OctoOps is already running (pid {holder.get('pid', '?')}, "
            f"started {holder.get('started', '?')}).\n"
            "Stop that instance first — if it's the scheduled task: "
            "schtasks /End /TN OctoOps",
            file=sys.stderr,
        )
        return 1
    try:
        return _run(args, config_path, paths)
    finally:
        lock.release()


def _disable_windows_quickedit() -> None:
    """Turn off console QuickEdit mode on Windows so the bot can't 'sleep'.

    With QuickEdit on (the conhost default) any click/selection in the window
    pauses every console write. Because logs are mirrored to the console inside
    the asyncio event loop, that pause stalls the whole loop — Telegram stops
    being answered until someone presses Enter to end the selection. Clearing
    the flag keeps it responsive 24/7. No-op off Windows or with no real console
    attached (e.g. headless under Task Scheduler), and never raises.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        # Declare signatures so the 64-bit HANDLE isn't truncated to 32 bits.
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        STD_INPUT_HANDLE = -10
        ENABLE_EXTENDED_FLAGS = 0x0080
        ENABLE_QUICK_EDIT_MODE = 0x0040

        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if not handle:
            return
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return  # no console (redirected/headless) — nothing to disable
        new_mode = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
        kernel32.SetConsoleMode(handle, new_mode)
    except Exception:  # noqa: BLE001 - a console tweak must never break startup
        pass


def _run(args: argparse.Namespace, config_path: Path, paths: AppPaths) -> int:
    """Setup (if needed), load config, configure logging, and serve."""
    if args.setup or not config_path.is_file():
        from octoops.wizard import run_wizard

        if not run_wizard(str(config_path), paths):
            print("Setup cancelled — no config written.", file=sys.stderr)
            return 1

        # Confirm the freshly written config end to end (including a live token
        # check) before starting, so a bad token surfaces now rather than as a
        # silent invalid_token_fatal after launch. Informational — we continue
        # regardless; a real config error is still caught by AppConfig.load below.
        from octoops.core.doctor import run_checks

        print()
        run_checks(config_path, paths, verify_token=True)
        print()

    try:
        config = AppConfig.load(config_path)
    except OctoOpsError as exc:
        # Logging may not be configured yet; print and exit non-zero.
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    # Load module secrets from the .env sidecar into the environment (a real
    # environment variable always wins via setdefault), e.g. BRAIN_API_KEY.
    from octoops.core.envfile import load_env_file

    env_secrets = load_env_file(config_path.parent / ".env")
    for key, value in env_secrets.items():
        os.environ.setdefault(key, value)

    from octoops.core.config import module_secret_values

    secrets = [config.telegram.bot_token]
    if config.mcp.token:
        secrets.append(config.mcp.token)
    secrets.extend(env_secrets.values())  # scrub module secrets from logs too
    # Also scrub secrets hand-placed in config.toml [modules.<name>] (e.g. an
    # api_key fallback) — the .env path is preferred but not enforced.
    secrets.extend(module_secret_values(config.module_sections))
    configure_logging(
        paths.resolve(config.core.log_file),
        config.core.log_max_bytes,
        # Mirror logs to stderr only for interactive runs. Headless (Task
        # Scheduler) has no TTY; mirroring there would duplicate the whole
        # rotated app log into the un-rotated run.bat stdout redirect.
        dev_stderr=sys.stderr.isatty(),
        secrets=secrets,
    )
    log = get_logger("octoops.main")
    log.info("octoops.home", path=str(paths.home))

    try:
        runtime = build_runtime(config, paths)
    except OctoOpsError as exc:
        log.error("bootstrap.failed", error=str(exc))
        return 1

    # Keep an interactive Windows console from freezing the loop on a stray click.
    _disable_windows_quickedit()

    try:
        asyncio.run(_serve(runtime))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        # The WhatsApp bridge is launched with asyncio.create_subprocess_exec,
        # which on Windows works only on the Proactor loop — the Selector loop
        # raises NotImplementedError for subprocesses, silently breaking both the
        # setup pairing flow and the runtime bridge supervisor. Proactor is the
        # default since 3.8; set it explicitly so the requirement is documented
        # and survives environments that changed the default. add_signal_handler
        # is unsupported on either Windows loop and already falls back in _serve.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(main())
