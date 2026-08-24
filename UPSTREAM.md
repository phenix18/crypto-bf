# Provenance and attribution

This repository is a working fork of **[HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader)**.

| | |
|---|---|
| Upstream | https://github.com/HKUDS/AI-Trader |
| Imported commit | `d03ff6c056b32ced735adf7c19ed8175adb1c8df` |
| Upstream date | 2026-06-11 |
| Import commit here | see `git log --reverse --oneline \| head -1` |

Everything under `service/`, `skills/`, `docs/`, `research/`, and `assets/`
originates upstream, except where later commits in this repository say
otherwise. `git log` is the authoritative record of what has been changed
since the import.

## Licensing — needs a decision before redistribution

The upstream README displays an MIT badge, but **the upstream repository
contains no `LICENSE` file** at the imported commit. A badge is not a licence
grant, so the terms this code may be redistributed under are not established
by the source tree.

No licence has been added here, deliberately: picking one on the upstream
authors' behalf would be a claim this fork is not in a position to make.
Before publishing or redistributing this fork, resolve it — for example by
asking the upstream maintainers to add the `LICENSE` file the badge refers to,
or by opening an issue upstream pointing at the discrepancy.

## Keeping up with upstream

```bash
git remote add upstream https://github.com/HKUDS/AI-Trader.git
git fetch upstream
git log --oneline HEAD..upstream/main   # what has landed upstream since
```

Upstream history is not a parent of this repository's history (the import is a
snapshot, not a merge), so bringing changes across means cherry-picking or
merging with `--allow-unrelated-histories`.
