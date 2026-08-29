; Colours for Zig, for textfold.
;
; Taken from tree-sitter-zig v1.1.2 (queries/highlights.scm), MIT licensed,
; Copyright (c) 2024 Amaan Qureshi <amaanq12@gmail.com>. Three changes, and
; every one of them is the sort of thing to expect when you bring a query
; over from another editor:
;
; 1. `#lua-match?` is written as `#match?`, in three places. The first is
;    Neovim's; tree-sitter does not evaluate it, so the pattern fires
;    unconditionally and every identifier in the file comes out as a type.
;    `#match?` is tree-sitter's own and textfold answers it. The patterns
;    themselves — `^[A-Z_][a-zA-Z0-9_]*`, `^[A-Z][A-Z_0-9]+$` and `^//!` —
;    mean the same in both, so the name is the whole of the translation.
;
; 2. `(identifier) @variable` has moved from the top of the file to the
;    bottom. See the note down there.
;
; 3. The screaming-snake-case `@constant` rule has moved above the
;    PascalCase `@type` rule. See the note beside it.
;
; The last two are the same point twice: Neovim resolves an overlap in
; favour of the pattern written later, and textfold in favour of the one
; written earlier — which is tree-sitter's own convention, and how a query
; puts a special case in front of a catch-all. A query written for the other
; rule needs its patterns in the other order.
;
; This is why a grammar plugin ships its query rather than copying whatever
; it downloads. The library is fetched and compiled on your machine because
; it has to be; the query is here because it is the half you are likely to
; have to adjust, and it is worth being able to read the diff.

; Parameters

(parameter
  name: (identifier) @variable.parameter)

; Constants, before types
;
; `MAX_ITEMS` matches both this and the PascalCase rule below it, and the
; earlier pattern is the one textfold keeps — so the two have to be written
; in the order of how specific they are. Upstream has this the other way
; round and relies on Neovim preferring the later pattern.

((identifier) @constant
  (#match? @constant "^[A-Z][A-Z_0-9]+$"))

; Types

(parameter
  type: (identifier) @type)

((identifier) @type
  (#match? @type "^[A-Z_][a-zA-Z0-9_]*"))

(variable_declaration
  (identifier) @type
  "="
  [
    (struct_declaration)
    (enum_declaration)
    (union_declaration)
    (opaque_declaration)
  ])

[
  (builtin_type)
  "anyframe"
] @type.builtin

; Constants

[
  "null"
  "unreachable"
  "undefined"
] @constant.builtin

(field_expression
  .
  member: (identifier) @constant)

(enum_declaration
  (container_field
    type: (identifier) @constant))

; Labels

(block_label (identifier) @label)

(break_label (identifier) @label)

; Fields

(field_initializer
  .
  (identifier) @variable.member)

(field_expression
  (_)
  member: (identifier) @variable.member)

(container_field
  name: (identifier) @variable.member)

(initializer_list
  (assignment_expression
      left: (field_expression
              .
              member: (identifier) @variable.member)))

; Functions

(builtin_identifier) @function.builtin

(call_expression
  function: (identifier) @function.call)

(call_expression
  function: (field_expression
    member: (identifier) @function.call))

(function_declaration
  name: (identifier) @function)

; Modules

(variable_declaration
  (identifier) @module
  (builtin_function
    (builtin_identifier) @keyword.import
    (#any-of? @keyword.import "@import" "@cImport")))

; Builtins

[
  "c"
  "..."
] @variable.builtin

((identifier) @variable.builtin
  (#eq? @variable.builtin "_"))

(calling_convention
  (identifier) @variable.builtin)

; Keywords

[
  "asm"
  "defer"
  "errdefer"
  "test"
  "error"
  "const"
  "var"
] @keyword

[
  "struct"
  "union"
  "enum"
  "opaque"
] @keyword.type

[
  "async"
  "await"
  "suspend"
  "nosuspend"
  "resume"
] @keyword.coroutine

"fn" @keyword.function

[
  "and"
  "or"
  "orelse"
] @keyword.operator

"return" @keyword.return

[
  "if"
  "else"
  "switch"
] @keyword.conditional

[
  "for"
  "while"
  "break"
  "continue"
] @keyword.repeat

[
  "usingnamespace"
  "export"
] @keyword.import

[
  "try"
  "catch"
] @keyword.exception

[
  "volatile"
  "allowzero"
  "noalias"
  "addrspace"
  "align"
  "callconv"
  "linksection"
  "pub"
  "inline"
  "noinline"
  "extern"
  "comptime"
  "packed"
  "threadlocal"
] @keyword.modifier

; Operator

[
  "="
  "*="
  "*%="
  "*|="
  "/="
  "%="
  "+="
  "+%="
  "+|="
  "-="
  "-%="
  "-|="
  "<<="
  "<<|="
  ">>="
  "&="
  "^="
  "|="
  "!"
  "~"
  "-"
  "-%"
  "&"
  "=="
  "!="
  ">"
  ">="
  "<="
  "<"
  "&"
  "^"
  "|"
  "<<"
  ">>"
  "<<|"
  "+"
  "++"
  "+%"
  "-%"
  "+|"
  "-|"
  "*"
  "/"
  "%"
  "**"
  "*%"
  "*|"
  "||"
  ".*"
  ".?"
  "?"
  ".."
] @operator

; Literals

(character) @character

([
  (string)
  (multiline_string)
] @string
  (#set! "priority" 95))

(integer) @number

(float) @number.float

(boolean) @boolean

(escape_sequence) @string.escape

; Punctuation

[
  "["
  "]"
  "("
  ")"
  "{"
  "}"
] @punctuation.bracket

[
  ";"
  "."
  ","
  ":"
  "=>"
  "->"
] @punctuation.delimiter

(payload "|" @punctuation.bracket)

; Comments

(comment) @comment @spell

((comment) @comment.documentation
  (#match? @comment.documentation "^//!"))

; Variables
;
; Last, not first. Upstream opens with this rule, because Neovim resolves an
; overlap in favour of the pattern written *later*; textfold resolves it in
; favour of the one written *earlier*, which is tree-sitter's own convention
; and how a query puts a special case in front of a catch-all. Left at the
; top, `(identifier) @variable` claims every name in the file before any of
; the rules above get a look in, and `Point`, `MAX_ITEMS` and `distance` all
; come out the colour of a local variable.
;
; Moving it here is the whole fix. It is the one adjustment worth expecting
; when you bring a query over from another editor, and the reason a grammar
; plugin ships its query instead of copying whatever it downloaded.

(identifier) @variable
