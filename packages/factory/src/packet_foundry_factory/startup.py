"""Explicitly requested per-user startup installation; never runs as SYSTEM/root."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path


def install(config: Path, *, platform: str | None = None) -> Path:
    platform = platform or sys.platform
    config = config.expanduser().resolve(strict=True)
    command = [sys.executable, "-m", "packet_foundry_factory.cli", "run", "--config", str(config)]
    if platform.startswith("linux"):
        import shlex
        target = Path.home() / ".config/systemd/user/packet-foundry-factory.service"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "[Unit]\nDescription=Packet Foundry Factory\nAfter=network-online.target\n\n"
            "[Service]\nType=simple\nExecStart=" + shlex.join(command)
            + "\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", target.name], check=True)
        return target
    if platform == "darwin":
        target = Path.home() / "Library/LaunchAgents/org.packet-foundry.factory.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        logs = Path.home() / "Library/Logs/PacketFoundry"
        logs.mkdir(parents=True, exist_ok=True)
        target.write_bytes(plistlib.dumps({
            "Label": "org.packet-foundry.factory", "ProgramArguments": command,
            "RunAtLoad": True, "KeepAlive": True,
            "StandardOutPath": str(logs / "factory.log"),
            "StandardErrorPath": str(logs / "factory.err.log"),
            "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        }))
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(target)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)], check=True)
        return target
    if platform == "win32":
        # One current-user task. Absolute native Python path; no powershell/shell interpolation.
        subprocess.run(["schtasks", "/Create", "/TN", "PacketFoundryFactory", "/SC", "ONLOGON",
                        "/TR", subprocess.list2cmdline(command), "/RL", "LIMITED", "/F"], check=True)
        # Laptop defaults otherwise stop the task on battery or after three days.
        # Fixed operator script: no configuration or credential values enter shell source.
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference='Stop'; "
            "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) "
            "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
            "Set-ScheduledTask -TaskName 'PacketFoundryFactory' -Settings $settings | Out-Null"],
            check=True)
        subprocess.run(["schtasks", "/Run", "/TN", "PacketFoundryFactory"], check=True)
        return config
    raise ValueError("startup management supports Linux, macOS and Windows")


def uninstall(*, platform: str | None = None) -> None:
    platform = platform or sys.platform
    if platform.startswith("linux"):
        subprocess.run(["systemctl", "--user", "disable", "--now", "packet-foundry-factory.service"], check=True)
        (Path.home() / ".config/systemd/user/packet-foundry-factory.service").unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    elif platform == "darwin":
        target = Path.home() / "Library/LaunchAgents/org.packet-foundry.factory.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(target)], check=False)
        target.unlink(missing_ok=True)
    elif platform == "win32":
        subprocess.run(["schtasks", "/End", "/TN", "PacketFoundryFactory"], check=False)
        subprocess.run(["schtasks", "/Delete", "/TN", "PacketFoundryFactory", "/F"], check=True)
    else:
        raise ValueError("unsupported startup platform")
