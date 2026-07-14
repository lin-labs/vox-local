---
name: local-guide
description: Query and maintain the shared concierge knowledge base — hidden-gem travel recommendations and customer profiles — when handling a booking request in a Puffo channel, recommending restaurants/onsen/experiences, looking up a guest's preferences or emergency constraints, or recording something learned about a guest. Trigger on booking threads, "recommend", "any good spots in <city>", guest names/account numbers, or dietary/emergency questions.
---

# Local Guide — Hidden Gems & Guest Profiles

## Locate the knowledge base (do this first)

This skill ships inside the `voice-local` repo at `skills/local-guide/`, so
the repo root is **two directories above this SKILL.md** — resolve symlinks,
since agent homes often symlink skill directories:

```bash
# <skill-dir> = the base directory of this skill (your harness tells you this)
CKB_REPO="${CKB_REPO:-$(cd "$(dirname "$(readlink -f "<skill-dir>/SKILL.md")")/../.." && pwd)}"
CKB="$CKB_REPO/bin/ckb"
```

Overrides: `$CKB_REPO` points at an alternate checkout; `$CKB_ROOT` points the
CLI at an alternate `kb/` data directory. If resolution fails, clone
`https://github.com/lin-labs/concierge-kb` and set `CKB_REPO`.

`ckb` is stdlib-only `python3`, JSON on stdout — no venv or install step.

## Before fulfilling any booking request

1. **Load the guest brief** so you act on their real preferences and constraints:
   ```bash
   $CKB profile brief <account-or-phone>
   ```
   Respect `Constraints & Emergency` absolutely (allergies, broth restrictions,
   contact-hours). If there's no profile, create one:
   ```bash
   $CKB profile upsert <account> --set name="..." --set 'phones=[+1...]'
   ```

2. **Check the gem KB before searching the open web** — it encodes booking
   method, phone numbers, and insider timing the web won't give you:
   ```bash
   $CKB gems search --city kobe --q "wagyu dinner" # ranked, voice-ready pitches
   $CKB gems get kobe-yazawa-teppan                # full detail + insider notes
   ```
   `booking:` tells you how to secure it: `phone` → dispatch the voice caller /
   call it yourself; `online`/`via-hotel` → handle in-channel.

## After the interaction

- **Record what you learned about the guest** (new preference, dislike, plan):
  ```bash
  $CKB profile note <account> "One-sentence durable fact."
  ```
- **Add gems you discovered** (from the guest, the fulfiller, or verified research):
  ```bash
  $CKB gems add --name "..." --city kyoto --pitch "one speakable sentence" \
      --tags food,quiet --booking phone --phone +81... --source caller
  ```
- **Commit and push** so other machines and agents see it:
  ```bash
  git -C "$CKB_REPO" add -A && git -C "$CKB_REPO" commit -m "kb: ..." && git -C "$CKB_REPO" push
  ```

## Privacy

Profiles are PII. Quote into the Puffo channel only what the booking needs
(name, party size, dietary constraint) — never the whole profile or emergency
contacts.
