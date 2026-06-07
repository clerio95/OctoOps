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

    secrets = [config.telegram.bot_token]
    if config.mcp.token:
        secrets.append(config.mcp.token)
    secrets.extend(env_secrets.values())  # scrub module secrets from logs too
    configure_logging(
        paths.resolve(config.core.log_file),
        config.core.log_max_bytes,
        secrets=secrets,
    )
    log = get_logger("octoops.main")
    log.info("octoops.home", path=str(paths.home))

    try:
        runtime = build_runtime(config, paths)
    except OctoOpsError as exc:
        log.error("bootstrap.failed", error=str(exc))
        return 1

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
