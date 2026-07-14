// Prefill from the active tab (title + selection), POST to voice-local /api/gems.

const $ = (id) => document.getElementById(id);

async function prefill() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  $("name").value = (tab.title || "").split(/[|\-–·]/)[0].trim().slice(0, 80);
  try {
    const [{ result } = {}] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => window.getSelection()?.toString() || "",
    });
    if (result) $("pitch").value = result.trim().slice(0, 400);
  } catch (_) { /* chrome:// pages etc. — prefill is best-effort */ }
  const { lastCity = "" } = await chrome.storage.sync.get("lastCity");
  $("city").value = lastCity;
  window._gemUrl = tab.url || "";
}

async function save() {
  const { endpoint = "", token = "" } = await chrome.storage.sync.get(["endpoint", "token"]);
  const status = $("status");
  if (!endpoint || !token) {
    status.className = "err";
    status.innerHTML = "Set the endpoint + token in the extension options first.";
    return;
  }
  const gem = {
    name: $("name").value.trim(),
    city: $("city").value.trim().toLowerCase(),
    pitch: $("pitch").value.trim(),
    tags: $("tags").value.trim(),
    details: $("details").value.trim(),
    url: window._gemUrl || "",
  };
  if (!gem.name || !gem.city || !gem.pitch) {
    status.className = "err";
    status.textContent = "Name, city, and pitch are required.";
    return;
  }
  $("save").disabled = true;
  status.className = ""; status.textContent = "Saving…";
  try {
    const r = await fetch(`${endpoint.replace(/\/$/, "")}/api/gems`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(gem),
    });
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || r.status);
    await chrome.storage.sync.set({ lastCity: gem.city });
    status.className = "ok";
    status.textContent = `Saved as ${body.gem.id} — your concierge can recommend it now.`;
  } catch (e) {
    status.className = "err";
    status.textContent = `Failed: ${e.message}`;
  } finally {
    $("save").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", prefill);
$("save").addEventListener("click", save);
