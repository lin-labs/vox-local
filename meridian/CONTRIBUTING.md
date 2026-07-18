# Contributing to Meridian

Meridian is a normal folder in the `vox-local` repository. The original
unrelated `origin/meridian` branch is import history only; do not add new work
there.

```bash
git fetch origin
git switch -c vincent/<feature> origin/meridian-dev
make meridian-setup
make meridian-dev
```

All web changes stay under `meridian/`. Before pushing:

```bash
make meridian-check
git add meridian
git commit -m "meridian: <what changed>"
git push -u origin HEAD
```

Open the pull request against `main`.
