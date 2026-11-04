# Fe 26.5 generic contract item limitation

`generic-contract.fe` is the smallest repro found while building Arena. The desired syntax is a generic high-level contract item so `GenericMatch<TicTacToe>` and `GenericMatch<Nim>` can be emitted directly. Fe 26.5.0 (`3dab95f`) rejects the type parameter list at parse time.

Reproduce with `fe check --standalone compiler-bugs/generic-contract.fe`.

Arena therefore puts the entire match implementation in generic `MatchBook` methods and keeps three thin concrete contract shells. Calls such as `book.reveal<TicTacToe>(...)` are monomorphized by Fe while storage, commit/reveal, timeout, and message semantics remain one implementation.
