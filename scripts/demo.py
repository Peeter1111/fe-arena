#!/usr/bin/env python3
"""Build Arena, deploy TicTacToeMatch locally, then launch the native Fe game."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TOOLCHAIN = ROOT.parent.parent / "toolchain"
DEFAULT_FE = LOCAL_TOOLCHAIN / "local" / "bin" / "fe"
DEFAULT_FOUNDRY = LOCAL_TOOLCHAIN / "foundry"


def executable(env_name: str, command: str, local: Path | None = None) -> str:
    configured = os.environ.get(env_name)
    if configured:
        return configured
    found = shutil.which(command)
    if found:
        return found
    if local and local.exists():
        return str(local)
    raise SystemExit(f"missing {command}; set {env_name} or put {command} on PATH")


FE = executable("FE", "fe", DEFAULT_FE)
CAST = executable("CAST", "cast", DEFAULT_FOUNDRY / "cast")
ANVIL = executable("ANVIL", "anvil", DEFAULT_FOUNDRY / "anvil")
PORT = int(os.environ.get("ARENA_ANVIL_PORT", "8558"))
RPC = f"http://127.0.0.1:{PORT}"


def run(args: list[str], cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def wait_for_rpc(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("anvil exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("timed out waiting for anvil")


def native_env() -> dict[str, str]:
    env = dict(os.environ)
    requested = env.get("ARENA_SDKROOT")
    if requested:
        env["SDKROOT"] = requested
    elif sys.platform == "darwin":
        sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk")
        if sdk.exists():
            env["SDKROOT"] = str(sdk)
    return env


def main() -> int:
    subprocess.check_call([FE, "build", "-O", "2", "--out-dir", "out"], cwd=ROOT)
    subprocess.check_call(
        [FE, "build", "--backend", "native", "--out-dir", "out"],
        cwd=ROOT / "demo",
        env=native_env(),
    )

    anvil = subprocess.Popen(
        [ANVIL, "--silent", "--port", str(PORT)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_rpc(anvil)
        accounts = json.loads(run([CAST, "rpc", "--rpc-url", RPC, "eth_accounts"]))
        sender = accounts[0]
        bytecode = (ROOT / "out" / "TicTacToeMatch.bin").read_text().strip()
        if bytecode.startswith("0x"):
            bytecode = bytecode[2:]
        constructor = run([CAST, "abi-encode", "f(uint256)", "0"])
        if constructor.startswith("0x"):
            constructor = constructor[2:]
        receipt = json.loads(
            run(
                [
                    CAST,
                    "send",
                    "--rpc-url",
                    RPC,
                    "--unlocked",
                    "--from",
                    sender,
                    "--create",
                    "0x" + bytecode + constructor,
                    "--json",
                ]
            )
        )
        address = receipt["contractAddress"]
        gas = int(receipt["gasUsed"], 16)
        print(f"local TicTacToeMatch: {address} (deploy gas {gas})", flush=True)
        print(
            "native demo uses the same Game implementation and generic solver",
            flush=True,
        )
        print(flush=True)
        return subprocess.call([str(ROOT / "demo" / "out" / "arena_demo")], cwd=ROOT)
    finally:
        anvil.terminate()
        try:
            anvil.wait(timeout=5)
        except subprocess.TimeoutExpired:
            anvil.kill()
            anvil.wait()


if __name__ == "__main__":
    sys.exit(main())
