"""LiteLLM proxy lifecycle (AGENTS.md §5.4).

Start the proxy as a subprocess bound to loopback, health-gate it, and tear it
down reliably (no orphans) on exit, quit, or signal — including child workers on
Windows.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .constants import HEALTH_PATH, LOOPBACK_HOST
from .paths import proxy_log_file


class ProxyError(RuntimeError):
    pass


class ProxyManager:
    """Owns a single LiteLLM proxy subprocess.

    Usage:
        pm = ProxyManager(config_path, port, env)
        pm.start()
        pm.wait_healthy()
        ...
        pm.stop()
    """

    def __init__(
        self,
        config_path: Path,
        port: int,
        env: dict[str, str],
        host: str = LOOPBACK_HOST,
        log_path: Optional[Path] = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.port = port
        self.host = host
        self.env = env
        self.log_path = log_path or proxy_log_file()
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None

    # -- command ------------------------------------------------------------
    def _command(self) -> list[str]:
        """Resolve how to invoke the litellm proxy.

        Prefer the console script if on PATH; otherwise `python -m litellm`.
        """
        cli = shutil.which("litellm")
        base = [cli] if cli else [sys.executable, "-m", "litellm"]
        return [
            *base,
            "--config",
            str(self.config_path),
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc else None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._proc is not None:
            raise ProxyError("Proxy already started")
        if not self.config_path.exists():
            raise ProxyError(f"Config not found: {self.config_path}")

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = open(self.log_path, "w", encoding="utf-8")

        kwargs: dict = dict(
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=self.env,
        )
        # On Windows, a new process group lets us signal the whole tree.
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True

        try:
            self._proc = subprocess.Popen(self._command(), **kwargs)
        except FileNotFoundError as exc:
            self._close_log()
            raise ProxyError(f"Could not start LiteLLM: {exc}") from exc

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _health_once(self) -> bool:
        url = f"{self.base_url}{HEALTH_PATH}"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as exc:
            # An HTTP response at all means the server is up.
            return 200 <= exc.code < 500
        except Exception:
            return False

    def wait_healthy(self, timeout: float = 60.0, interval: float = 0.5) -> None:
        """Poll the liveliness endpoint until ready or raise ProxyError."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                raise ProxyError(
                    f"LiteLLM exited before becoming healthy "
                    f"(code {self._proc.returncode if self._proc else '?'}). "
                    f"See log: {self.log_path}"
                )
            if self._health_once():
                return
            time.sleep(interval)
        self.stop()
        raise ProxyError(
            f"LiteLLM did not become healthy within {timeout:.0f}s. "
            f"See log: {self.log_path}"
        )

    def stop(self, timeout: float = 10.0) -> None:
        """Terminate the proxy (and any children) and release the log handle."""
        proc = self._proc
        if proc is None:
            self._close_log()
            return
        try:
            if proc.poll() is None:
                self._terminate_tree(proc, timeout)
        finally:
            self._proc = None
            self._close_log()

    def _terminate_tree(self, proc: subprocess.Popen, timeout: float) -> None:
        if sys.platform == "win32":
            # taskkill cleans up the whole tree of uvicorn workers.
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                proc.kill()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
            return

        # POSIX: signal the process group, escalating to SIGKILL.
        import os
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            finally:
                self._log_handle = None

    # context manager sugar
    def __enter__(self) -> "ProxyManager":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
