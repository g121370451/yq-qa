from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from typing import Iterable


def cleanup_ports(ports: Iterable[int], current_pid: int | None = None) -> None:
    current_pid = current_pid or os.getpid()
    for port in sorted({int(port) for port in ports if int(port) > 0}):
        pids = _listening_pids(port)
        for pid in sorted(pids):
            if pid == current_pid:
                continue
            print(
                f"[rag-openviking-bot] cleanup: terminating pid {pid} on port {port}",
                flush=True,
            )
            _terminate_pid(pid)
        if pids:
            _wait_port_free(port)


def _listening_pids(port: int) -> set[int]:
    if os.name == "nt":
        return _listening_pids_windows(port)
    return _listening_pids_posix(port)


def _listening_pids_windows(port: int) -> set[int]:
    command = (
        "Get-NetTCPConnection "
        f"-LocalPort {port} "
        "-State Listen "
        "-ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    return _parse_pid_lines(result.stdout)


def _listening_pids_posix(port: int) -> set[int]:
    for command in (
        ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
        ["fuser", f"{port}/tcp"],
    ):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        pids = _parse_pid_lines(result.stdout)
        if pids:
            return pids
    return set()


def _parse_pid_lines(text: str) -> set[int]:
    pids: set[int] = set()
    for token in text.replace("\r", "\n").split():
        try:
            pids.add(int(token))
        except ValueError:
            continue
    return pids


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _wait_port_free(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) != 0:
                return
        time.sleep(0.2)
