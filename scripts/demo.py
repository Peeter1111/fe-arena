#!/usr/bin/env python3
"""Play a full Tic-Tac-Toe match on local Anvil against Fe's native solver."""

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
TOOLCHAIN = ROOT.parent.parent / "toolchain"
FE_DEFAULT = TOOLCHAIN / "local" / "bin" / "fe"
FOUNDRY = TOOLCHAIN / "foundry"


def executable(env: str, command: str, local: Path) -> str:
    configured = os.environ.get(env)
    if configured:
        return configured
    found = shutil.which(command)
    if found:
        return found
    if local.exists():
        return str(local)
    raise SystemExit(f"missing {command}; set {env} or put {command} on PATH")


FE = executable("FE", "fe", FE_DEFAULT)
CAST = executable("CAST", "cast", FOUNDRY / "cast")
ANVIL = executable("ANVIL", "anvil", FOUNDRY / "anvil")
PORT = int(os.environ.get("ARENA_ANVIL_PORT", "8558"))
RPC = f"http://127.0.0.1:{PORT}"
SOLVER = ROOT / "solver_cli" / "out" / "arena_solver_cli"


def run(args: list[str], cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def native_env() -> dict[str, str]:
    env = dict(os.environ)
    if env.get("ARENA_SDKROOT"):
        env["SDKROOT"] = env["ARENA_SDKROOT"]
    elif sys.platform == "darwin":
        sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk")
        if sdk.exists():
            env["SDKROOT"] = str(sdk)
    return env


def wait_for_rpc(process: subprocess.Popen[bytes]) -> None:
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError("anvil exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("timed out waiting for anvil")


def send(sender: str, target: str, signature: str, *values: object) -> dict:
    receipt = json.loads(run([
        CAST, "send", "--rpc-url", RPC, "--unlocked", "--from", sender,
        target, signature, *[str(v) for v in values], "--json",
    ]))
    if receipt.get("status") != "0x1":
        raise RuntimeError(f"transaction reverted: {receipt}")
    return receipt


def deploy(sender: str) -> tuple[str, int]:
    bytecode = (ROOT / "out" / "TicTacToeMatch.bin").read_text().strip()
    constructor = run([CAST, "abi-encode", "f(uint256)", "3600"])
    receipt = json.loads(run([
        CAST, "send", "--rpc-url", RPC, "--unlocked", "--from", sender,
        "--create", "0x" + bytecode.removeprefix("0x") + constructor.removeprefix("0x"),
        "--json",
    ]))
    if receipt.get("status") != "0x1" or not receipt.get("contractAddress"):
        raise RuntimeError(f"deployment failed: {receipt}")
    return receipt["contractAddress"], int(receipt["gasUsed"], 16)


def state(address: str, match_id: int) -> tuple[int, int]:
    raw = run([CAST, "call", "--rpc-url", RPC, address, "state(uint256)", str(match_id)])
    payload = raw.removeprefix("0x")
    if len(payload) != 128:
        raise RuntimeError(f"unexpected state response: {raw}")
    return int(payload[:64], 16), int(payload[64:], 16)


def solver_move(word: int) -> int:
    reply = subprocess.run(
        [str(SOLVER)], input=f"{word}\n", cwd=ROOT,
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    if reply not in [str(i) for i in range(9)]:
        raise RuntimeError(f"invalid native Fe solver reply: {reply!r}")
    move = int(reply)
    if (word >> (move * 2)) & 3:
        raise RuntimeError("solver selected an occupied square")
    return move


def board(word: int) -> list[str]:
    cells = []
    for pos in range(9):
        value = (word >> (2 * pos)) & 3
        cells.append(str(pos) if value == 0 else ("X" if value == 1 else "O"))
    return [" ".join(cells[i:i + 3]) for i in (0, 3, 6)]


def main() -> int:
    subprocess.check_call(
        [FE, "build", "-O", "2", "--out-dir", "out"],
        cwd=ROOT, stdout=subprocess.DEVNULL,
    )
    subprocess.check_call(
        [FE, "build", "--backend", "native", "--out-dir", "out"],
        cwd=ROOT / "solver_cli", env=native_env(), stdout=subprocess.DEVNULL,
    )
    anvil = subprocess.Popen(
        [ANVIL, "--silent", "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    transcript: list[str] = []

    def record(line: str = "") -> None:
        print(line, flush=True)
        transcript.append(line)

    try:
        wait_for_rpc(anvil)
        accounts = json.loads(run([CAST, "rpc", "--rpc-url", RPC, "eth_accounts"]))
        x, o = accounts[:2]
        address, deploy_gas = deploy(x)
        receipt = send(x, address, "create(address)(uint256)", o)
        match_id = 1
        word, winner = state(address, match_id)
        record("Arena / deployed Tic-Tac-Toe against Fe's native best_move solver")
        record(f"Anvil contract: {address}")
        record(f"deployment gas: {deploy_gas}; create gas: {int(receipt['gasUsed'], 16)}")
        record("X uses local account 0; solver O uses local account 1")
        record("match 1")
        for line in board(word):
            record(line)
        record()

        # Deterministic legal human moves; the solver chooses every O move.
        human_moves = iter([0, 1, 3])
        turn_count = 0
        while winner == 0 and turn_count < 9:
            x_turn = (word & (1 << 18)) == 0
            if x_turn:
                move = next(human_moves, None)
                if move is None or (word >> (2 * move)) & 3:
                    move = next(i for i in range(9) if ((word >> (2 * i)) & 3) == 0)
                who, sender = "X", x
            else:
                move = solver_move(word)
                who, sender = "O (Fe solver)", o
            receipt = send(sender, address, "play(uint256,uint8)(uint8)", match_id, move)
            next_word, winner = state(address, match_id)
            if next_word == word:
                raise RuntimeError("on-chain play did not advance state")
            word = next_word
            turn_count += 1
            record(f"{who} plays {move}; tx gas {int(receipt['gasUsed'], 16)}; state {word}; winner {winner}")
            for line in board(word):
                record(line)
            record()
        if winner == 0:
            raise RuntimeError("match exceeded nine turns without a result")
        record(f"final on-chain result: {'X' if winner == 1 else 'O' if winner == 2 else 'draw'}")
        record(f"saved demo/transcript.txt")
        path = ROOT / "demo" / "transcript.txt"
        path.write_text("\n".join(transcript) + "\n")
        return 0
    finally:
        anvil.terminate()
        try:
            anvil.wait(timeout=5)
        except subprocess.TimeoutExpired:
            anvil.kill()
            anvil.wait()


if __name__ == "__main__":
    sys.exit(main())
