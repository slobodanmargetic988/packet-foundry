#!/usr/bin/env python3
"""Verify release wheels in a disposable native environment, without starting services."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

PACKAGES = (
    "packet-foundry-contracts",
    "packet-foundry-collector",
    "packet-foundry-warehouse",
    "packet-foundry-registry",
    "packet-foundry-factory",
)
SERVICES = ("collector", "warehouse", "registry", "factory")
ROOT = Path(__file__).resolve().parent.parent


def verify(wheel_directory: Path, python_version: str) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv must be installed to verify isolated wheel installation")
    wheel_directory = wheel_directory.resolve(strict=True)
    wheels = []
    for package in PACKAGES:
        matches = list(wheel_directory.glob(package.replace("-", "_") + "-*.whl"))
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one built wheel for {package}")
        with zipfile.ZipFile(matches[0]) as archive:
            for notice in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
                paths = [name for name in archive.namelist()
                         if name.endswith(".dist-info/licenses/" + notice)]
                if len(paths) != 1 or archive.read(paths[0]) != (ROOT / notice).read_bytes():
                    raise RuntimeError(f"{package} wheel is missing the canonical {notice}")
        wheels.append(str(matches[0]))

    with tempfile.TemporaryDirectory(prefix="packet-foundry-install-") as temporary:
        directory = Path(temporary)
        constraints = directory / "constraints.txt"
        environment = directory / "venv"
        subprocess.run([
            uv, "export", "--locked", "--all-packages", "--all-extras", "--no-dev",
            "--no-emit-workspace", "--no-hashes", "--quiet", "--output-file", str(constraints),
        ], cwd=ROOT, check=True)
        subprocess.run([uv, "venv", "--python", python_version, str(environment)],
                       cwd=directory, check=True)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        subprocess.run([
            uv, "pip", "install", "--python", str(python), "--find-links", str(wheel_directory),
            "--constraint", str(constraints), *wheels, "packet-foundry-factory[tracing]",
        ], cwd=directory, check=True)

        # Import from the installed wheels, outside the workspace. This catches
        # missing packaged files and accidental editable/source-tree dependencies.
        imports = [package.replace("-", "_") for package in PACKAGES]
        program = (
            "import importlib, pathlib, sys; "
            f"modules = {imports!r}; "
            "root = pathlib.Path(sys.prefix).resolve(); "
            "assert all(pathlib.Path(importlib.import_module(name).__file__).resolve()"
            ".is_relative_to(root) for name in modules); "
            "import langsmith; "
            "print('All five packages and optional LangSmith SDK import from the isolated install.')"
        )
        subprocess.run([str(python), "-c", program], cwd=directory, check=True)
        for service in SERVICES:
            executable = binaries / (f"foundry-{service}.exe" if os.name == "nt"
                                     else f"foundry-{service}")
            result = subprocess.run([str(executable), "--help"], cwd=directory,
                                    text=True, capture_output=True, timeout=30, check=True)
            if not result.stdout.strip():
                raise RuntimeError(f"installed {service} entrypoint produced no help")
            print(f"Installed foundry-{service} --help passed.")
    print("Isolated wheel installation verified; temporary environment removed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-directory", type=Path, default=ROOT / "dist")
    parser.add_argument("--python", default="3.12")
    args = parser.parse_args()
    verify(args.wheel_directory, args.python)


if __name__ == "__main__":
    main()
