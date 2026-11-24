## Checks

Run `make check` before each commit and each pull request. It is the one entry point for the checks.
Run `make hooks` once in each clone. Run it again after `uv cache clean`, because the hooks record
the path of the pre-commit tool in the uv cache.

Each language adds a Make file named `make/<language>.mk`. The file adds its targets to
`CHECK_TARGETS`. The main `Makefile` does not change when you add a language.

`make length` runs the `length` hook, because the hook installs `scc`. So the hook is the shared
definition of this check, and it cannot call `make length`, which would recurse. A hook for any other
check calls its Make target. Use `language: unsupported` for such a hook.

`make length` also checks untracked files that git does not ignore. Add build output to `.gitignore`.

Limits:

- A source file has at most 500 code lines. `scc` counts code lines. Blank lines and comments do
  not count. A Python docstring that starts a line counts as a comment.
- A Python function has a McCabe complexity of at most 10. Ruff enforces it.
- A Go function has a cyclomatic complexity of at most 10. `cyclop` enforces it.
- A Rust function has a cognitive complexity of at most 15. Clippy enforces it.
