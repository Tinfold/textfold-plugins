; A rebase plan, coloured the way the plan panel colours it — a verb read in
; the buffer and the same verb read in the panel should not be two different
; colours for the same decision.
;
;   pick     keyword      it stays as it is
;   reword   function     it stays, the message changes
;   edit     attribute    it stops here
;   squash   type         it folds into the one above
;   fixup    type
;   drop     comment      it goes, and reads as gone
;
; The sha is quiet, because nobody reads a rebase plan by its shas. The
; subject is left uncoloured on purpose: it is the part you actually read, and
; ordinary text is what reads best.
;
; Where two patterns claim the same bytes the earlier one wins, so the rules
; about a particular verb come first and the catch-all for a sha comes last.

; ---- what happens to a commit

((operation (command) @keyword)
 (#match? @keyword "^(p|pick)$"))

((operation (command) @function)
 (#match? @function "^(r|reword)$"))

((operation (command) @attribute)
 (#match? @attribute "^(e|edit)$"))

((operation (command) @type)
 (#match? @type "^(s|squash|f|fixup)$"))

; A commit that is going reads as gone: the verb, the sha and the subject.
; Two patterns rather than one, because a `#match?` constrains every node the
; capture it names claims — asking `@comment` to be `drop` in a pattern that
; also captures the sha as `@comment` asks the sha to be `drop` too, and the
; whole pattern then matches nothing at all.
((operation (command) @comment)
 (#match? @comment "^(d|drop)$"))

((operation (command) @_drop (label) @comment (message)? @comment)
 (#match? @_drop "^(d|drop)$"))

; ---- what happens to the rebase itself

((operation (command) @keyword.control)
 (#match? @keyword.control "^(x|exec|b|break|l|label|t|reset|m|merge|u|update-ref|noop)$"))

; A name somebody chose — a label, or the ref `update-ref` tracks — rather
; than a sha, so it is not greyed out with the shas below.
((operation (command) @_named (label) @constant)
 (#match? @_named "^(l|label|t|reset|u|update-ref)$"))

; ---- the rest

(operation (label) @comment)

(option) @operator

(comment) @comment
