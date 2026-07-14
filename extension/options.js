const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", async () => {
  const { endpoint = "", token = "" } = await chrome.storage.sync.get(["endpoint", "token"]);
  $("endpoint").value = endpoint;
  $("token").value = token;
});

$("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    endpoint: $("endpoint").value.trim(),
    token: $("token").value.trim(),
  });
  $("status").textContent = "Saved.";
  setTimeout(() => ($("status").textContent = ""), 1500);
});
