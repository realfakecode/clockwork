# calc — Design decisions

Canonical, normative. Decisions and constraints only — no description of the code. Each
entry is addressable as `D-N`; workers cite these, the design session adds to them.

## D-1: Operator precedence and associativity

`*` binds tighter than binary `+` and `-`. All binary operators are left-associative.
Parentheses `( )` override precedence.

Worked examples the implementation must satisfy: `2 + 3 * 4` = 14; `10 - 4 - 3` = 3;
`(2 + 3) * 4` = 20.

## D-2: Values are integers

The core expression language operates on integers: the operators `+`, `-`, `*`, and `/`
are in scope, and every result is an integer.
