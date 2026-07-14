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

# --- serve: webhook /call + MCP /mcp against the same throwaway kb ----------
port=7797
"$ckb" serve --port "$port" 2>/dev/null &
serve_pid=$!
trap 'kill "$serve_pid" 2>/dev/null; rm -rf "$tmp"' EXIT
for _ in $(seq 1 20); do curl -sf "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break; sleep 0.2; done

post() { curl -sf -X POST "http://127.0.0.1:$port/$1" -H 'Content-Type: application/json' -d "$2"; }
check "serve healthz"           '"ok": true'            curl -sf "http://127.0.0.1:$port/healthz"
check "serve schema"            "search_hidden_gems"    curl -sf "http://127.0.0.1:$port/tools/schema"
check "serve /call search"      "kobe-yazawa-teppan"    post call '{"name":"search_hidden_gems","arguments":{"city":"kobe","query":"wagyu"}}'
check "serve /call note writes" '"ok": true'            post call '{"name":"remember_about_caller","arguments":{"key":"123456","note":"Serve smoke note."}}'
check "serve /call unknown"     "unknown tool"          post call '{"name":"nope"}'
check "serve /mcp initialize"   "concierge-kb"          post mcp '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}'
check "serve /mcp tools/list"   "inputSchema"           post mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
check "serve /mcp tools/call"   "Yazawa"                post mcp '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_hidden_gem","arguments":{"id":"kobe-yazawa-teppan"}}}'
kill "$serve_pid" 2>/dev/null

# --- puffo bridge: message grammar -> tool calls (no network) ---------------
bridge="$here/bin/ckb-puffo-bridge"
check "bridge parse search"     '"city": "kobe"'         "$bridge" --parse-only "search kobe quiet onsen"
check "bridge parse get"        "get_hidden_gem"         "$bridge" --parse-only "get kobe-yazawa-teppan"
check "bridge parse raw json"   "add_hidden_gem"         "$bridge" --parse-only '{"name": "add_hidden_gem", "arguments": {"name": "X", "city": "kobe", "pitch": "y"}}'
check "bridge parse help"       "commands:"              "$bridge" --parse-only "help"

exit $fail
