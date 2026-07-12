#!/usr/bin/env bash
# Smoke test: exercises every ckb command against a throwaway copy of kb/.
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -r "$here/kb" "$tmp/kb"
export CKB_ROOT="$tmp/kb"
ckb="$here/bin/ckb"
fail=0

check() { # check <desc> <expected-substring> <cmd...>
  local desc="$1" want="$2"; shift 2
  local got; got="$("$@" 2>&1)"
  if [[ "$got" == *"$want"* ]]; then echo "ok   $desc"; else
    echo "FAIL $desc"; echo "     want substring: $want"; echo "     got: ${got:0:300}"; fail=1
  fi
}

check "search by city+query"      "kobe-yazawa-teppan"      "$ckb" gems search --city kobe --q "wagyu dinner"
check "search by tag"             "arima-kin-no-yu"         "$ckb" gems search --tag onsen
check "get full gem"              "insider notes"           bash -c "$ckb gems get kobe-yazawa-teppan | python3 -c 'import json,sys; print(list(json.load(sys.stdin)[\"sections\"]))'"
check "get missing gem errors"    "no gem"                  "$ckb" gems get nope-nope
check "add gem"                   '"ok": true'              "$ckb" gems add --name "Test Spot" --city testcity --pitch "A test." --tags test
check "added gem searchable"      "testcity-test-spot"      "$ckb" gems search --city testcity
check "profile by account"        "Boyan Lin"               "$ckb" profile get 123456
check "profile by phone"          "Boyan Lin"               "$ckb" profile get "+1 (650) 656-7722"
check "brief has constraints"     "Constraints"             "$ckb" profile brief 123456
check "brief respects vegetarian" "Vegetarian"              "$ckb" profile brief 208090
check "note appends"              '"ok": true'              "$ckb" profile note 123456 "Smoke test note."
check "note visible in brief"     "Smoke test note"         "$ckb" profile brief 123456
check "upsert creates"            '"ok": true'              "$ckb" profile upsert 999999 --set name="Temp Guest" --set "phones=[+15550000000]"
check "upsert lookup by phone"    "Temp Guest"              "$ckb" profile get "+15550000000"
check "tools schema valid json"   "search_hidden_gems"      bash -c "$ckb tools schema | python3 -c 'import json,sys; d=json.load(sys.stdin); assert all(t[\"type\"]==\"function\" for t in d); print(\" \".join(t[\"name\"] for t in d))'"

exit $fail
