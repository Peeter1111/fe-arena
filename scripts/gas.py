#!/usr/bin/env python3
"""Measure Arena direct-play gas on a fresh local Anvil chain."""

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
PORT = int(os.environ.get("ARENA_ANVIL_PORT", "8557"))
RPC = f"http://127.0.0.1:{PORT}"


def run(args: list[str], *, text: bool = True) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=text).strip()


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


def send(sender: str, target: str, signature: str, *args: object) -> dict:
    raw = run(
        [
            CAST,
            "send",
            "--rpc-url",
            RPC,
            "--unlocked",
            "--from",
            sender,
            target,
            signature,
            *[str(arg) for arg in args],
            "--json",
        ]
    )
    receipt = json.loads(raw)
    if receipt.get("status") != "0x1":
        raise RuntimeError(f"transaction reverted: {receipt}")
    return receipt


def deploy(sender: str, contract: str, timeout: int = 3600) -> tuple[str, int]:
    bytecode = (ROOT / "out" / f"{contract}.bin").read_text().strip()
    if bytecode.startswith("0x"):
        bytecode = bytecode[2:]
    constructor = run([CAST, "abi-encode", "f(uint256)", str(timeout)])
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
    if receipt.get("status") != "0x1" or not receipt.get("contractAddress"):
        raise RuntimeError(f"deployment failed: {receipt}")
    return receipt["contractAddress"], int(receipt["gasUsed"], 16)


def gas_used(receipt: dict) -> int:
    return int(receipt["gasUsed"], 16)


def measure_game(
    sender: str, opponent: str, contract: str, moves: list[int]
) -> dict[str, object]:
    address, deploy_gas = deploy(sender, contract)
    send(sender, address, "create(address)(uint256)", opponent)
    match_id = 1
    plays: list[int] = []
    for index, move_code in enumerate(moves):
        player = sender if index % 2 == 0 else opponent
        receipt = send(
            player,
            address,
            "play(uint256,uint8)(uint8)",
            match_id,
            move_code,
        )
        plays.append(gas_used(receipt))
    return {
        "contract": contract,
        "address": address,
        "deployment_gas": deploy_gas,
        "moves": moves,
        "play_gas": plays,
        "average_move_gas": sum(plays) // len(plays),
    }


def main() -> int:
    subprocess.check_call([FE, "build", "-O", "2", "--out-dir", "out"], cwd=ROOT)
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
        opponent = accounts[1]
        games = [
            ("TicTacToeMatch", [0, 3, 1, 4, 2]),
            ("Connect4Match", [0, 1, 0, 1, 0]),
            ("NimMatch", [16, 8, 17, 0, 16]),
        ]
        measurements = [
            measure_game(sender, opponent, contract, moves)
            for contract, moves in games
        ]
    finally:
        anvil.terminate()
        try:
            anvil.wait(timeout=5)
        except subprocess.TimeoutExpired:
            anvil.kill()
            anvil.wait()

    output = {
        "fe_version": run([FE, "--version"]),
        "anvil_version": run([ANVIL, "--version"]).splitlines()[0],
        "optimization": 2,
        "sender": sender,
        "opponent": opponent,
        "games": measurements,
    }
    output_path = ROOT / "gas-results.json"
    output_path.write_text(json.dumps(output, indent=2) + "\n")

    print("game             direct move avg")
    for item in measurements:
        print(f"{item['contract']:<17}{item['average_move_gas']:>15}")
    print(f"wrote {output_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
