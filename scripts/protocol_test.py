#!/usr/bin/env python3
"""Exercise deadline and caller edge cases against fresh Anvil deployments."""

from __future__ import annotations

import json
import os
import subprocess

import gas


gas.PORT = int(os.environ.get("ARENA_ANVIL_PORT", "8559"))
gas.RPC = f"http://127.0.0.1:{gas.PORT}"


def reject(sender: str, target: str, signature: str, *values: object) -> None:
    result = subprocess.run(
        [
            gas.CAST, "send", "--rpc-url", gas.RPC, "--unlocked",
            "--from", sender, "--gas-limit", "500000", target, signature,
            *[str(v) for v in values], "--json",
        ],
        cwd=gas.ROOT, text=True, capture_output=True,
    )
    if result.returncode == 0:
        receipt = json.loads(result.stdout)
        assert receipt.get("status") != "0x1", f"unexpected success: {receipt}"


def advance(seconds: int) -> None:
    gas.run([gas.CAST, "rpc", "--rpc-url", gas.RPC, "evm_increaseTime", str(seconds)])
    gas.run([gas.CAST, "rpc", "--rpc-url", gas.RPC, "evm_mine"])


def state(target: str, match_id: int) -> tuple[int, int]:
    raw = gas.run([
        gas.CAST, "call", "--rpc-url", gas.RPC,
        target, "state(uint256)", str(match_id),
    ]).removeprefix("0x")
    assert len(raw) == 128
    return int(raw[:64], 16), int(raw[64:], 16)


def digest(match_id: int, word: int, move: int, secret: int, player: str) -> str:
    encoded = gas.run([
        gas.CAST, "abi-encode", "f(uint256,uint256,uint256,uint256,address)",
        str(match_id), str(word), str(move), str(secret), player,
    ])
    return gas.run([gas.CAST, "keccak", encoded])


def main() -> int:
    subprocess.check_call([gas.FE, "build", "-O", "2", "--out-dir", "out"],
                          cwd=gas.ROOT, stdout=subprocess.DEVNULL)
    anvil = subprocess.Popen(
        [gas.ANVIL, "--silent", "--port", str(gas.PORT)],
        cwd=gas.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        gas.wait_for_rpc(anvil)
        players = json.loads(gas.run([gas.CAST, "rpc", "--rpc-url", gas.RPC, "eth_accounts"]))
        p1, p2 = players[:2]
        direct, _ = gas.deploy(p1, "TicTacToeMatch", timeout=20)
        hidden, _ = gas.deploy(p1, "RpsMatch", timeout=20)
        for target in (direct, hidden):
            reject(p1, target, "create(address)(uint256)", p1)
            reject(p1, target, "create(address)(uint256)", "0x0000000000000000000000000000000000000000")
        print("both protocols reject same-player and zero-address matches")

        # No move: the waiting player wins after the initial creation deadline.
        gas.send(p1, direct, "create(address)(uint256)", p2)
        reject(p2, direct, "claimTimeout(uint256)(uint8)", 1)
        advance(30)
        gas.send(p2, direct, "claimTimeout(uint256)(uint8)", 1)
        assert state(direct, 1) == (0, 2)
        reject(p1, direct, "play(uint256,uint8)(uint8)", 1, 0)
        print("direct: no first move, early claim, and post-timeout play checked")

        # Every legal play resets the clock; the former deadline cannot win.
        gas.send(p1, direct, "create(address)(uint256)", p2)
        advance(10)
        gas.send(p1, direct, "play(uint256,uint8)(uint8)", 2, 0)
        advance(10)
        reject(p1, direct, "claimTimeout(uint256)(uint8)", 2)
        advance(20)
        gas.send(p1, direct, "claimTimeout(uint256)(uint8)", 2)
        assert state(direct, 2)[1] == 1
        print("direct: clock resets after play; waiting player can claim")

        # No commitments at all: neither acted, so the round is a draw.
        gas.send(p1, hidden, "create(address)(uint256)", p2)
        advance(30)
        gas.send(p1, hidden, "claimTimeout(uint256)(uint8)", 1)
        assert state(hidden, 1) == (0, 3)
        print("hidden: no commits yields a draw")

        # One player commits; the absent commit is a forfeit.
        gas.send(p1, hidden, "create(address)(uint256)", p2)
        gas.send(p1, hidden, "commit(uint256,uint256)", 2, digest(2, 0, 0, 11, p1))
        advance(30)
        gas.send(p1, hidden, "claimTimeout(uint256)(uint8)", 2)
        assert state(hidden, 2) == (0, 1)
        print("hidden: missing second commitment forfeits the match")

        # Both commit, one reveals, and the remaining reveal is too late.
        gas.send(p1, hidden, "create(address)(uint256)", p2)
        gas.send(p1, hidden, "commit(uint256,uint256)", 3, digest(3, 0, 0, 17, p1))
        gas.send(p2, hidden, "commit(uint256,uint256)", 3, digest(3, 0, 2, 23, p2))
        gas.send(p1, hidden, "reveal(uint256,uint8,uint256)(uint8)", 3, 0, 17)
        assert state(hidden, 3) == (0, 0)
        advance(30)
        reject(p2, hidden, "reveal(uint256,uint8,uint256)(uint8)", 3, 2, 23)
        gas.send(p1, hidden, "claimTimeout(uint256)(uint8)", 3)
        assert state(hidden, 3) == (0, 1)
        print("hidden: late reveal rejected; unanswered reveal forfeits the match")
        return 0
    finally:
        anvil.terminate()
        try:
            anvil.wait(timeout=5)
        except subprocess.TimeoutExpired:
            anvil.kill()
            anvil.wait()


if __name__ == "__main__":
    raise SystemExit(main())
