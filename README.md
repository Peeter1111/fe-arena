# Arena

**Pitch:** Define a deterministic game once in Fe, then reuse the exact same rules for an on-chain commit/reveal match engine, a perfect-play solver, and differential tests.

**Fe highlight:** One associated-type `Game` trait drives both generic EVM `MatchBook` methods and the native `solve<G>` solver, while contract capabilities stay explicit in `uses (...)`.

Arena is a small game engine built to make Fe's traits, associated types, generics, effects, messages, storage, hashing, testing, and native backend visible in one project. It ships three compact games, a shared on-chain match protocol, a generic alpha-beta solver, a 10,200-move contract-vs-rules differential test, measured gas, and a native terminal demo.

**Verified locally:** 5/5 tests pass; the seeded differential replay covers 10,200 contract moves; optimal Tic-Tac-Toe is a draw; and measured committed-move gas ranges from 114,359 (Nim) to 127,074 (Mini Connect Four).

Quick proof:

```sh
fe --version
fe test --jobs 1
python3 scripts/gas.py
python3 scripts/demo.py
```

The project was built and measured with **Fe 26.5.0 (3dab95f)**. The two Python commands also need Foundry's `anvil` and `cast` on `PATH` (or `ANVIL`/`CAST` environment variables).

## The problem

Small on-chain games usually duplicate their rules: one copy decides what the contract accepts, another drives a bot or solver, and another powers tests or a UI. Those copies drift. A legal move in one layer can become illegal in another.

Arena makes the game rules the single source of truth. A game implements one pure `Game` trait. The contract engine, solver, native demo, and test harness all call that implementation directly.

This is useful beyond the three bundled games: it is a compact example of how Fe can express reusable domain logic once while still compiling concrete EVM contracts with narrow, explicit capabilities.

## Approach

The core interface has an associated state type and only pure operations:

```fe
pub trait Game {
    type State: Copy
    fn initial() -> Self::State
    fn encode(_ state: Self::State) -> u256
    fn decode(_ word: u256) -> Self::State
    fn turn(_ state: Self::State) -> u8
    fn legal(_ state: Self::State, _ mv: u8) -> bool
    fn apply(_ state: Self::State, _ mv: u8) -> Self::State
    fn winner(_ state: Self::State) -> u8
    fn move_space() -> u8
}
```

Each game keeps its board in one word on chain:

| Game | Fe source | Form | Formatted LOC |
| --- | --- | --- | ---: |
| Tic-Tac-Toe | `src/tictactoe.fe` | 9 cells, 2 bits/cell + turn bit | 69 |
| Mini Connect Four | `src/connect4.fe` | 4×4 board, connect 4, 2 bits/cell + turn bit | 78 |
| Nim | `src/nim.fe` | 1-3-5 heaps, 4 bits/heap + turn bit | 57 |

The core library is **498 Fe lines** excluding `src/tests.fe` and the native demo.

The on-chain protocol is the same for every game:

1. `create(opponent)` creates a match and stores the game's initial word.
2. The player whose turn it is sends `commit(id, hash)`.
3. The same player sends `reveal(id, move, secret)`; the contract verifies `keccak256(id, move, secret, player)`, checks the shared game rules, and advances the compact state.
4. A missed reveal can be resolved with `claimTimeout(id)`.
5. `state(id)` returns the compact state word and winner code.

The match protocol does not include wagers or token transfers. It is deliberately a game-state primitive rather than a gambling contract.

## Fe highlight

The main Fe-specific idea is **generic domain logic plus explicit effects**.

`MatchBook` is one storage type whose game-dependent methods are parameterized by `G: Game`:

```fe
pub fn reveal<G: Game>(
    mut self,
    _ id: u256,
    _ move_code: u8,
    _ secret: u256,
    _ caller: Address,
) -> u8
```

The concrete EVM shells specialize it:

```fe
Reveal { id, move_code, secret } -> u8 uses (ctx, mut book) {
    book.reveal<TicTacToe>(id, move_code, secret, ctx.caller())
}
```

That split shows several Fe features working together:

- **Traits and associated types:** every game gets its own pure state type without forcing the solver to know its layout.
- **Generics/monomorphization:** `solve<G: Game>`, `MatchBook::create<G>`, `commit<G>`, `reveal<G>`, and timeout logic are written once and compiled for concrete games.
- **Effects:** pure game code has no EVM capability. Contract handlers request `Ctx` only when they need caller identity or time, and request mutable storage only when they write.
- **Message groups:** one `ArenaMsg` defines the ABI shared by all three concrete contracts.
- **StorageMap:** match records are keyed by match ID while each game board remains a single `u256`.
- **`keccak_words`:** commitments use Fe's word-oriented Keccak helper without hand-written ABI buffers.
- **Native backend:** the same game and solver modules are dependencies of a real AArch64/macOS terminal executable.

Fe 26.5 does **not** currently parse a generic high-level contract item such as `contract GenericMatch<G: Game>`. The minimal reproducer is in `compiler-bugs/generic-contract.fe`. Arena therefore puts all shared behavior in generic `MatchBook` methods and keeps three thin concrete contract declarations. This preserves one implementation of the protocol while staying within the compiler's current contract syntax.

## Architecture

| Path | Role |
| --- | --- |
| `src/game.fe` | Pure `Game` trait and generic compact-word helpers |
| `src/tictactoe.fe` | Tic-Tac-Toe rules |
| `src/connect4.fe` | 4×4 Connect Four rules |
| `src/nim.fe` | 1-3-5 Nim rules and XOR helper |
| `src/solver.fe` | Generic alpha-beta negamax solver over `Game` |
| `src/match_engine.fe` | Generic match storage and create/commit/reveal/timeout protocol |
| `src/contracts.fe` | Shared message group and three thin concrete contracts |
| `src/tests.fe` | Solver, protocol, and 10,200-move differential tests |
| `demo/` | Native Fe terminal program reusing `arena` as a path dependency |
| `scripts/gas.py` | Fresh-chain gas reproducer |
| `scripts/demo.py` | One-command local deployment + native playable demo |
| `compiler-bugs/` | Minimal Fe 26.5 generic-contract parser reproducer |

There are two execution paths over the same rules:

```text
                         +------------------+
                         |  Game trait      |
                         |  pure Fe rules   |
                         +---------+--------+
                                   |
                     +-------------+-------------+
                     |                           |
              +------v------+             +------v------+
              | MatchBook   |             | solve<G>    |
              | methods <G> |             | alpha-beta  |
              | EVM storage |             |             |
              +------+------+             +------+------+
                     |                           |
             concrete contracts              native CLI
```

## Build and test

Install Fe 26.5.0 with the official `feup` installer and install Foundry if
you want to run the Anvil demo or gas measurement. Arena itself uses only the
Fe standard library; there are no package dependencies in the main ingot.
During development, `anvil` and `cast` were kept in the contest toolchain
directory at `../../toolchain/foundry/`; both scripts also accept normal
`PATH` installations or explicit `ANVIL` / `CAST` environment variables.

```sh
fe --version
# fe 26.5.0 (3dab95f)

fe fmt --check
fe check
fe test --jobs 1
fe build -O 2
```

The starter CI installs the pinned Fe release with:

```sh
curl -fsSL https://raw.githubusercontent.com/argotorg/fe/master/feup/feup.sh | bash -s -- --version v26.5.0 --no-modify-path
```

The current full test result is:

```text
PASS commit_reveal_and_timeout_work
PASS compact_games_have_expected_winners
PASS differential_replay_10200_moves
PASS nim_solver_matches_xor_theorem
PASS tic_tac_toe_perfect_play_is_a_draw

test result: ok. 5 passed; 0 failed
```

Fe 26.5's native backend is usable as a command-line target here, not just as
an internal compiler mode. `fe build --backend native` emits a host executable
on x86-64 Linux and AArch64 macOS. The standard library's `std::io::host()`
provides byte input/output through explicit `Read` and `Write` effects, and
the demo imports Arena as an ordinary path dependency. Native output is
separate from EVM contract output, so Arena builds the contracts with the
default Sonatina backend and the terminal demo with the native backend.

The solver checks two known game-theory facts:

- optimal Tic-Tac-Toe from the empty board is a draw;
- for every non-empty position reachable by only removing stones from the bundled 1-3-5 Nim start (47 positions), the generic solver reports a losing position exactly when the heap XOR is zero.

The differential test uses fixed seeds and makes **3,400 legal moves per game, 10,200 total**. Every move goes through `commit` and `reveal` on a deployed contract in Fe's EVM test sandbox. After each move the test asks the contract for its word and winner, then compares both with a direct call to the same game's pure rules.

Run just that check with:

```sh
fe test --jobs 1 -f differential_replay_10200_moves
```

### Measured gas

Gas was measured on a fresh local Anvil 1.9.0 chain from `fe build -O 2`. Each row is the integer average of five legal committed moves. "Move" means one `commit` transaction plus its `reveal` transaction; deployment and match creation are excluded.

| Game | Avg commit gas | Avg reveal gas | Avg gas / committed move |
| --- | ---: | ---: | ---: |
| Tic-Tac-Toe | 80,012 | 41,678 | **121,690** |
| Mini Connect Four | 81,220 | 45,853 | **127,074** |
| Nim | 78,823 | 35,535 | **114,359** |

The exact per-transaction values, move sequences, deployment gas, tool versions, and optimization level are committed in `gas-results.json`. Reproduce them from a clean local chain with:

```sh
python3 scripts/gas.py
```

`scripts/gas.py` starts and stops its own Anvil process and uses only unlocked local development accounts. It never reads a key file.

## Demo

Run:

```sh
python3 scripts/demo.py
```

The command:

1. builds all three EVM contracts with `-O 2`;
2. builds the Fe native demo executable;
3. starts an isolated local Anvil process;
4. deploys `TicTacToeMatch` with a zero-second reveal timeout as a deployment smoke test;
5. launches the native terminal game, where the human is X and `best_move<TicTacToe>` is O;
6. stops Anvil on exit.

The terminal play loop is native rather than an RPC client: it reuses the exact `TicTacToe` rules and generic solver through a local Fe ingot dependency, while the local-chain deployment proves the contract artifact is deployable. Contract behavior itself is exercised by the commit/reveal and 10,200-move tests.

A captured run is committed as `demo/transcript.txt`. The interesting part looks like:

```text
Arena / Tic-Tac-Toe
you are X; the generic perfect-play solver is O
0 1 2
3 4 5
6 7 8
move 0-8: X 1 2
3 4 5
6 7 8
solver: 4
...
O wins
```

On the development AArch64 Mac, the Command Line Tools defaulted to a macOS 27 SDK that its own linker could not parse because of an `arm64e.x1-macos` target in the SDK text stubs. An installed macOS 26.5 SDK worked. `scripts/demo.py` selects that SDK automatically when it exists; on another affected Mac, set `ARENA_SDKROOT` to a compatible installed SDK. This affects only Fe's experimental native link step, not EVM builds or tests.

## Limitations

- Fe 26.5 cannot express the desired generic high-level `contract GenericMatch<G: Game>` syntax. The protocol is genuinely generic in `MatchBook`, but three small concrete contract shells are required. See `compiler-bugs/`.
- Fe 26.5 native executables are host-targeted: executable output is supported on x86-64 Linux and AArch64 macOS, not as a portable contract runtime.
- The solver is intentionally simple alpha-beta search without memoization. Tic-Tac-Toe and the tested Nim spaces are fast; solving the full initial 4×4 Connect Four tree is not part of the test suite.
- The native terminal demo does not submit its human/solver turns to the deployed Anvil contract. It demonstrates reuse of the same pure Fe rules and solver; contract equivalence is covered separately by the differential test.
- Commit/reveal hides a move only between the two transactions. It is not a general-purpose anti-front-running protocol, and a production application would need to choose timeout rules and economic incentives for its threat model.
- Match state is intentionally compact and minimal. There is no matchmaking service, indexing UI, token transfer, wager, or upgrade mechanism.

The smallest compiler issue found while building Arena is reproducible with:

```sh
fe check --standalone compiler-bugs/generic-contract.fe
```

It is expected to fail on Fe 26.5.0 at the generic parameter list with `contract definition requires a body`.

## Declaration

- **Pre-existing work:** The project began after the contest build window opened by copying the official Fe Builders Challenge starter. That untouched starting point is commit `7595019`. All Arena game rules, solver, match engine, tests, measurements, scripts, demo, and documentation were added during the contest work.
- **Third-party code:** The official starter is the only code copied into this repository and retains its Apache-2.0 license. Fe 26.5 documentation, standard-library source, compiler tests, and examples were read as references; no other contest entrant code was read or copied. Foundry 1.9.0 was used as an external local-chain tool and is not vendored.
- **AI assistance:** ChatGPT worker chats assisted with research, implementation, testing, measurement, and documentation; Claude reviewed the project. A human review by the team lead remains required before any submission. See `AI_DISCLOSURE.md`.

## Team

Peeter Tsanev.

## License

Apache-2.0. See `LICENSE`.
