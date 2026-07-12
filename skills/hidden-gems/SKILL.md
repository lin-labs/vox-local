---
name: hidden-gems
description: Query and maintain the shared concierge knowledge base — hidden-gem travel recommendations and customer profiles — when handling a booking request in a Puffo channel, recommending restaurants/onsen/experiences, looking up a guest's preferences or emergency constraints, or recording something learned about a guest. Trigger on booking threads, "recommend", "any good spots in <city>", guest names/account numbers, or dietary/emergency questions.
---

# Hidden Gems & Guest Profiles

The shared memory for the concierge stack lives at
`/home/blin/Experiments/voice/concierge-kb` (env override: `CKB_ROOT` points at
its `kb/` dir). Use the `ckb` CLI — stdlib python3, JSON on stdout:

```bash
CKB=/home/blin/Experiments/voice/concierge-kb/bin/ckb
```

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
- **Commit** in the repo: `git -C /home/blin/Experiments/voice/concierge-kb add -A && git -C ... commit -m "..."`.

## Privacy

Profiles are PII. Quote into the Puffo channel only what the booking needs
(name, party size, dietary constraint) — never the whole profile or emergency
contacts.
