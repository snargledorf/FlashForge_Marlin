#!/usr/bin/env python3
"""FlashForge / Dremel Marlin Firmware Builder

Builds Marlin firmware using PlatformIO and encrypts the output binary
for FlashForge / Dremel printers by directly importing and calling
the encryption function.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Sequence

# Ensure repository root is on sys.path so the submodule can be imported
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flashforge_firmware_encryption_tool.flashforge_firmware_encryption_tool import (
    encrypt_firmware,
)

# Default configuration matching marlin_builder.sh
BUILD_ENV = "FF_F407ZG"
DEFAULT_ENCRYPTION_KEY = "flashforge790315"
SIZE_TOOL_NAME = "arm-none-eabi-size"

MACHINES: dict[str, dict[str, str]] = {
    "nx": {
        "flag": "-DFF_DREAMER_NX_MACHINE",
        "key": DEFAULT_ENCRYPTION_KEY,
    },
    "dreamer": {
        "flag": "-DFF_DREAMER_MACHINE",
        "key": DEFAULT_ENCRYPTION_KEY,
    },
    "inventor": {
        "flag": "-DFF_INVENTOR_MACHINE",
        "key": DEFAULT_ENCRYPTION_KEY,
    },
    "dremel": {
        "flag": "-DFF_DREMEL_3D20_MACHINE",
        "key": "flashforge123456",
    },
}

OPTIONS: dict[str, tuple[str, str]] = {
    "swap": ("-DFF_EXTRUDER_SWAP", "_swap"),
    "linear_advance": ("-DLIN_ADVANCE", "_la"),
    "classic_ui": ("-DUSE_OLD_MARLIN_UI", "_classic"),
    "old_mb": ("-DFF_DREAMER_OLD_MB", "_dir"),
    "mks_ui": ("-DUSE_MKS_UI", "_mks"),
    "dual_sd": ("-DFF_MULTI_SD", "_sd"),
}


class TrackOrderAction(argparse.Action):
    """Custom action to record option presence while preserving CLI specification order."""

    def __init__(self, option_strings: Sequence[str], dest: str, nargs: int = 0, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Sequence[str] | str | None,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, True)
        ordered: list[str] | None = getattr(namespace, "_ordered_options", None)
        if ordered is None:
            ordered = []
            setattr(namespace, "_ordered_options", ordered)
        if self.dest not in ordered:
            ordered.append(self.dest)


def get_fw_version(version_path: Path) -> str:
    """Extract SHORT_BUILD_VERSION string from Marlin/Marlin/Version.h."""
    if not version_path.is_file():
        return ""
    pattern = re.compile(r'SHORT_BUILD_VERSION\s+"([^"]+)"')
    try:
        with open(version_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                match = pattern.search(line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return ""


def find_platformio() -> list[str] | None:
    """Find platformio executable or module invocation."""
    for cmd in ["platformio", "pio"]:
        path = shutil.which(cmd)
        if path:
            return [path]

    # Check ~/.platformio virtual environment
    home = Path.home()
    if sys.platform == "win32":
        pio_penv = home / ".platformio" / "penv" / "Scripts" / "platformio.exe"
    else:
        pio_penv = home / ".platformio" / "penv" / "bin" / "platformio"
    if pio_penv.is_file():
        return [str(pio_penv)]

    # Check if platformio is available as a python module
    try:
        res = subprocess.run(
            [sys.executable, "-m", "platformio", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            return [sys.executable, "-m", "platformio"]
    except Exception:
        pass

    return None


def run_command(
    cmd: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    """Run command with platform-appropriate settings (e.g. Windows batch execution)."""
    use_shell = False
    if sys.platform == "win32" and cmd:
        prog = cmd[0]
        if prog.lower().endswith((".cmd", ".bat")):
            use_shell = True
    return subprocess.run(cmd, env=env, cwd=cwd, shell=use_shell)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="FlashForge / Dremel Marlin firmware builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
example:
  %(prog)s -m dreamer -s -l
""",
    )

    parser.add_argument(
        "-m",
        "--machine",
        dest="machine",
        type=str.lower,
        choices=list(MACHINES.keys()),
        required=True,
        help="machine name ( nx / dreamer / inventor / dremel )",
    )
    parser.add_argument(
        "-s",
        "--swap",
        dest="swap",
        action=TrackOrderAction,
        help="swap extruders ( for dreamer and inventor machines )",
    )
    parser.add_argument(
        "-o",
        "--old-mb",
        dest="old_mb",
        action=TrackOrderAction,
        help="use Dreamer old motherboard ( swap extruder DIR )",
    )
    parser.add_argument(
        "-l",
        "--linear-advance",
        dest="linear_advance",
        action=TrackOrderAction,
        help="enable linear advance ( pressure control algo )",
    )
    parser.add_argument(
        "-u",
        "--classic-ui",
        dest="classic_ui",
        action=TrackOrderAction,
        help="old style GUI",
    )
    parser.add_argument(
        "-g",
        "--mks-ui",
        dest="mks_ui",
        action=TrackOrderAction,
        help="MKS GUI",
    )
    parser.add_argument(
        "-d",
        "--dual-sd",
        dest="dual_sd",
        action=TrackOrderAction,
        help="Dual SD Support (EXPERIMENTAL!)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        help="verbose build",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="show build flags and commands without executing them",
    )

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)

    project_dir = REPO_ROOT / "Marlin"
    fw_path = project_dir / ".pio" / "build" / BUILD_ENV
    version_file = project_dir / "Marlin" / "Version.h"
    build_dir = REPO_ROOT / "build"

    # Verify PlatformIO is installed
    pio_cmd = find_platformio()
    if not pio_cmd and not args.dry_run:
        print("platformio is not installed", file=sys.stderr)
        return 1

    # Extract firmware version
    fw_version = get_fw_version(version_file)

    # Collect flags and filename postfix
    flags_list: list[str] = []
    name_postfix = ""

    ordered_options: list[str] = getattr(args, "_ordered_options", []) or []
    for opt_name in ordered_options:
        flag, postfix = OPTIONS[opt_name]
        flags_list.append(flag)
        name_postfix += postfix

    # Add machine-specific flag and select encryption key
    machine_config = MACHINES[args.machine]
    flags_list.append(machine_config["flag"])
    encryption_key = machine_config["key"]

    flags_str = " ".join(flags_list)

    # Configure environment
    env = os.environ.copy()
    env["PLATFORMIO_BUILD_FLAGS"] = flags_str
    print("Flags: " + flags_str)

    # Prepare PlatformIO clean and build commands
    clean_cmd = (pio_cmd or ["platformio"]) + [
        "run",
        "--project-dir",
        str(project_dir),
        "--target",
        "clean",
        "-e",
        BUILD_ENV,
    ]
    if not args.verbose:
        clean_cmd.append("--silent")

    build_cmd = (pio_cmd or ["platformio"]) + [
        "run",
        "--project-dir",
        str(project_dir),
        "-e",
        BUILD_ENV,
    ]
    if not args.verbose:
        build_cmd.append("--silent")

    date_str = datetime.now().strftime("%m%d%Y")
    version_part = f"_{fw_version}" if fw_version else ""
    out_filename = f"{args.machine}{version_part}{name_postfix}_{date_str}.bin"
    output_bin = build_dir / out_filename
    input_bin = fw_path / "firmware.bin"
    elf_path = fw_path / "firmware.elf"

    size_tool = shutil.which(SIZE_TOOL_NAME)

    if args.dry_run:
        print("[DRY-RUN] Clean command:    " + " ".join(clean_cmd))
        print("[DRY-RUN] Build command:    " + " ".join(build_cmd))
        if size_tool:
            print(f"[DRY-RUN] Size command:     {size_tool} --format=berkeley {elf_path}")
        print(f"[DRY-RUN] Encrypt call:     encrypt_firmware('{input_bin}', '{output_bin}', key=b'{encryption_key}')")
        print(f"[DRY-RUN] Target output:    {output_bin}")
        return 0

    print("Clean...")
    run_command(clean_cmd, env=env)

    print("Build...")
    build_res = run_command(build_cmd, env=env)

    if build_res.returncode == 0:
        print("Build OK\nEncode firmware...")

        if size_tool and elf_path.is_file():
            run_command([size_tool, "--format=berkeley", str(elf_path)])

        build_dir.mkdir(parents=True, exist_ok=True)

        if not input_bin.is_file():
            print(f"Error: Firmware binary not found at '{input_bin}'", file=sys.stderr)
            return 1

        key_bytes = encryption_key.encode("latin-1")
        try:
            encrypt_firmware(str(input_bin), str(output_bin), key_bytes)
        except Exception as e:
            print(f"Firmware encryption failed: {e}", file=sys.stderr)
            return 1
    else:
        print("Build failed", file=sys.stderr)
        return build_res.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
