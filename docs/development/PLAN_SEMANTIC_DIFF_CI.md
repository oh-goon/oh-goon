# Plan meaning changes in CI

To compare execution intent after both Plan files have been fully resolved:

```sh
oh-goon plan diff before.yml after.yml --exit-code --json
```

The exit status is `0` if resolved meaning is unchanged, `1` if it changed, and `2` for a rejected/invalid Plan. In scripts, handle `1` as a detected difference, not as a CLI crash. Omit `--exit-code` for backward-compatible behavior, where all valid comparisons exit `0`.

The JSON and human presentations are not changed by the flag. Semantic comparison is owned by the original resolver, not source-text comparison; implicit Plan names, imported Goons and resolved Steps can matter even if raw YAML appears similar. This command is read-only: it is **not** `plan run`, Enforcer admission, lock authorization or certification.
