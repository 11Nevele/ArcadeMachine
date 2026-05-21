#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import TextIO

from config import LauncherConfig


class ControllerBridgeError(RuntimeError):
    pass


class ControllerBridgeManager:
    def __init__(self, config: LauncherConfig):
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: TextIO | None = None
        self.last_status = "Controller bridge is disabled for development mode."

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def should_start(self) -> bool:
        return (
            self.config.is_linux
            and self.config.production_mode
            and self.config.enable_controller_bridge
        )

    def start(self) -> None:
        if self.is_running:
            return

        if not self.should_start():
            if self.config.production_mode and not self.config.is_linux:
                self.last_status = "Production mode is enabled on a non-Linux host. Bridge startup was skipped."
            else:
                self.last_status = "Controller bridge is disabled for development mode."
            return

        controller_script = self.config.controller_script
        if not controller_script.is_file():
            raise ControllerBridgeError(
                f"Controller bridge script was not found at {controller_script}."
            )

        log_warning: str | None = None
        try:
            self.config.bridge_log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_handle = self.config.bridge_log_path.open("a", encoding="utf-8")
        except OSError:
            self.log_handle = None
            log_warning = "the bridge log path is not writable"

        command = [
            "sudo",
            self.config.python_command,
            str(controller_script),
        ]
        bridge_env = os.environ.copy()
        bridge_env["ARCADE_CLOSE_SIGNAL_PATH"] = str(
            self.config.bridge_log_path.parent / "close_game.signal"
        )
        bridge_env["ARCADE_GAME_RUNNING_FLAG_PATH"] = str(
            self.config.bridge_log_path.parent / "game_running.flag"
        )

        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(self.config.project_root),
                env=bridge_env,
                stdout=self.log_handle if self.log_handle is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if self.log_handle is not None else subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError as exc:
            self._close_log_handle()
            raise ControllerBridgeError(
                "sudo is not available. Start the launcher on Linux from a shell that can elevate privileges."
            ) from exc

        if self.process.poll() is not None:
            self.stop()
            if self.log_handle is None:
                raise ControllerBridgeError(
                    "Controller bridge exited immediately. Log capture was disabled because the bridge log path is not writable."
                )
            raise ControllerBridgeError(
                f"Controller bridge exited immediately. Check {self.config.bridge_log_path} for details."
            )

        if log_warning is None:
            self.last_status = f"Controller bridge running with PID {self.process.pid}."
        else:
            self.last_status = (
                f"Controller bridge running with PID {self.process.pid}; log capture is disabled because {log_warning}."
            )

    def stop(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)

            self.process = None

        self._close_log_handle()
        self.last_status = "Controller bridge stopped."

    def _close_log_handle(self) -> None:
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None