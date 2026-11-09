# Arena

**One Fe ruleset, two honest match protocols.** Alternating-turn games use a single `play` transaction per move; a simultaneous game uses commitments because both players must choose before either move is disclosed. Pure Fe traits supply the rules to EVM contracts, a generic perfect-play solver for turn-based games, and contract-versus-rules tests.

Arena includes Tic-Tac-Toe, 4×4 Connect Four, 1-3-5 Nim, and best-of-three-round rock-paper-scissors (RPS). It has no wagers, tokens, external services, or real-money dependencies.

## Try it

Use Fe 26.5.0 and Foundry Anvil/Cast. The Python scripts find `fe`, `anvil` and `cast` on `PATH`, or use `FE`, `ANVIL` and `CAST`. They also detect the contest's local toolchain layout.

```sh
fe --version
fe fmt --check
fe check
fe build -O 2
fe test --jobs 1
python3 scripts/protocol_test.py
python3 scripts/gas.py
python3 scripts/demo.py
```

The last command builds the EVM contract and a small native Fe solver client, starts its own local Anvil, creates a Tic-Tac-Toe match between two unlocked development accounts, and submits **every X and O turn** to the deployed contract. X follows a reproducible scripted sequence; O's move comes from the Fe `best_move<TicTacToe>` solver run on the on-chain board word. After each transaction it reads `state(uint256)` from the contract. It writes the full match to [`demo/transcript.txt`](demo/transcript.txt), including transaction gas and the final on-chain winner.

The older interactive Fe terminal game is still in `demo/` and can be built separately:

```sh
cd demo
fe build --backend native --out-dir out
./out/arena_demo
```

On an affected macOS installation, specify `ARENA_SDKROOT` for `scripts/demo.py` or `SDKROOT` for a direct native build. An installed macOS 26.5 SDK works on the development Mac. The demo selects Apple's `cc` through `/usr/bin`; for a direct native build, ensure `cc` on `PATH` is a C compiler. These are native linker requirements.

## Rules and protocols

The pure trait `Game` has an associated `State` type, `initial`, `encode`/`decode`, `turn`, `legal`, `apply`, `winner`, and `move_space` methods. Tic-Tac-Toe, Mini Connect Four, and Nim each implement it. Each compact board fits in one `u256`. `MatchBook::play<G: Game>` verifies whose turn it is, checks the rule implementation, updates the board, and returns the winner. `solve<G: Game>` and `best_move<G: Game>` use alpha-beta negamax over the same rules; they never need EVM effects.

A distinct `HiddenGame` trait has `initial`, `encode`/`decode`, `legal`, `resolve(state, move1, move2)`, and `winner`. Its `resolve` method advances a round only after **both** players reveal. RPS uses moves 0 = rock, 1 = paper, 2 = scissors and a compact score/round word. A match finishes when either player wins two rounds or after three rounds, including drawn rounds; ties in total wins are a draw. The whole RPS implementation is in `src/rps.fe` (`wc -l src/rps.fe`).

The protocols have different ABIs:

| Turn-based `ArenaMsg` | Simultaneous `HiddenMsg` |
| --- | --- |
| `create(address)` | `create(address)` |
| `play(id, move_code)` | `commit(id, hash)` |
| `claimTimeout(id)` | `reveal(id, move_code, secret)` |
| `state(id)` | `claimTimeout(id)`, `state(id)` |

Both require distinct nonzero players. They start a deadline when the match is created and reset it after each successful state change. Actions require `block.timestamp < deadline`; a timeout may be claimed at `block.timestamp >= deadline`. The timeout duration is fixed per contract at deployment. Winner codes are 0 ongoing, 1 player one, 2 player two, 3 draw.

In turn-based games, **only the player waiting for a move** can claim the timeout. This works even if the player whose turn it is never sends anything; after a successful move the clock belongs to the next player. Expired plays and all actions after a final result revert.

For RPS, each player commits once, then each reveals once. The commitment binds the match ID, encoded round state, move, secret, and player address:

```fe
keccak_words([id, state, mv as u256, secret, player.word()])
```

The first commitment and the second commitment each reset the deadline, as do reveals. Neither player can reveal before **both** commitments exist. A reveal at or after the deadline **reverts** and cannot update the board, even if its hash is valid. `claimTimeout` resolves an expired phase: if just one player committed (or revealed), that player wins; if neither acted in that phase, the result is a draw. Once both reveal, their sealed moves are resolved together, the phase resets for the next round, and a new deadline begins. The match never waits forever for a missing commitment or a missing reveal.

These are match-state examples, not incentive-complete production games. Timeouts use block timestamps, and a player may deliberately forfeit or force a draw when both fail to act. Secrets must remain hidden until both commitments are posted; predictable secrets defeat move secrecy. There are no deposits to discourage stalling.

## Why this is Fe-specific

The rules have no EVM effects. Only storage-changing `MatchBook` and `HiddenBook` methods use `StorageMap`; their concrete contract handlers request `Ctx` for caller identity and time. The solver and the native command-line client can use the same game types without a Solidity port or an off-chain reimplementation.

Fe 26.5.0 rejects a high-level generic declaration such as `contract GenericMatch<G: Game>` (reproduce with `fe check --standalone compiler-bugs/generic-contract.fe`, which is expected to fail). That is why the three alternating-turn games each have a thin concrete contract shell around one generic `MatchBook`; the fourth shell uses `HiddenBook` because RPS needs a genuinely different simultaneous protocol.

Source map:

| Source | Purpose |
| --- | --- |
| `src/game.fe`, `src/hidden_game.fe` | Two small pure traits |
| `src/tictactoe.fe`, `src/connect4.fe`, `src/nim.fe`, `src/rps.fe` | Four pure rule implementations |
| `src/match_engine.fe`, `src/hidden_engine.fe` | Generic state machines, authorization, deadlines |
| `src/contracts.fe` | Fe message groups and four concrete wrappers |
| `src/solver.fe` | Generic turn-based alpha-beta solver |
| `src/tests.fe` | Pure-rule checks, actor-specific protocol tests, differential replay |
| `scripts/protocol_test.py` | On-chain expiry and invalid-caller checks on Anvil |
| `scripts/gas.py`, `gas-results.json` | Fresh-Anvil direct-move gas reproducer and per-transaction readings |
| `scripts/demo.py`, `solver_cli/`, `demo/transcript.txt` | Deployed-contract match against native Fe solver |
| `compiler-bugs/` | Minimal generic-contract parse failure |

## Tests

Run `fe test --jobs 1`: all eight tests pass with Fe 26.5.0. `fe test --jobs 1 -f differential_replay_all_four_games` checks 3,400 legal moves for **each** turn-based game (10,200 direct plays in all) and 600 fully committed and revealed RPS rounds, always comparing contract state and winner with pure Fe rules. Fixed seeds and direct contract calls make it reproducible. The test uses a small forwarding contract as the second actor, rather than calling both sides from the same address.

`python3 scripts/protocol_test.py` covers invalid opponents, early timeout attempts, missing first play, clock reset after a play, missing commitments, and an unanswered reveal. It advances Anvil's clock and proves that **a late reveal is rejected** before resolving the timeout. The script starts and stops an isolated local Anvil and uses only its unlocked development accounts.

The known game-theory checks are also inside `fe test --jobs 1`: the initial Tic-Tac-Toe position is a draw under perfect play; for all 47 nonempty heap combinations reachable by reducing the starting 1-3-5 Nim heaps, the solver's losing positions match heap XOR zero.

## Gas: one transaction instead of two

`python3 scripts/gas.py` builds with `-O 2`, starts fresh Anvil 1.9.0, and measures five legal direct moves per game, excluding deployment and creation. Its per-transaction measurements are saved in [`gas-results.json`](gas-results.json). The previous two-transaction commit/reveal averages below are preserved in `git show ddef9f6:gas-results.json`; both sets used Fe 26.5.0 and the same legal move sequences.

| Game | Previous commit + reveal / move | Direct `play` / move |
| --- | ---: | ---: |
| Tic-Tac-Toe | 121,690.4 | **50,270.6** |
| Mini Connect Four | 127,074.0 | **55,685.2** |
| Nim | 114,359.2 | **42,242.8** |

The old benchmark stored whole-gas averages with integer truncation; the table recomputes exact means from its per-transaction readings. The previous prototype allowed one address to play both sides, while the corrected measurements use two distinct unlocked development addresses. These figures demonstrate the cost of removing an unnecessary transaction in this local setup, not a universal mainnet fee prediction. No gas comparison is claimed for RPS; its commit/reveal protocol has a different purpose.

## Limits and provenance

Fe 26.5.0 is still an early compiler. Its generic contract syntax limitation has a minimal reproduction in `compiler-bugs/`, while native executable support is host-dependent. The perfect-play solver has no memoization: Tic-Tac-Toe and the tested Nim positions finish quickly, but exhaustive solving of the initial 4×4 Connect Four tree is not part of the project. The seeded differential test uses legal moves and is not a proof against all adversarial states.

The repository began with the official Fe Builders Challenge starter (commit `7595019`); the game rules, contracts, solver, tests, scripts, and documentation were written for this entry. No other entrants' code was copied. AI assistance and the pending human review are disclosed in [`AI_DISCLOSURE.md`](AI_DISCLOSURE.md).

Author: Peeter Tsanev. License: Apache-2.0, [`LICENSE`](LICENSE).
