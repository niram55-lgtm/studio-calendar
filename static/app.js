(function () {
  const $ = (id) => document.getElementById(id);
  const trainersDiv = $("trainers");

  function renderTrainers(trainers) {
    trainersDiv.innerHTML = "";
    for (const t of trainers) {
      const row = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = t.name;
      cb.checked = !!t.enabled;
      const span = document.createElement("span");
      span.textContent = t.name;
      const cnt = document.createElement("span");
      cnt.className = "count";
      cnt.textContent = t.class_count !== undefined ? ` (${t.class_count})` : "";
      row.appendChild(cb);
      row.appendChild(span);
      row.appendChild(cnt);
      trainersDiv.appendChild(row);
    }
  }

  // Initial render from server-rendered state.
  renderTrainers(window.STUDIO.initial_trainers.map(n => ({
    name: n, enabled: window.STUDIO.initial_enabled.includes(n),
  })));

  $("btn-load").addEventListener("click", async () => {
    const link = $("link").value.trim();
    $("load-status").textContent = "טוען...";
    const r = await fetch("/api/load", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({link}),
    });
    const data = await r.json();
    if (!r.ok) {
      $("load-status").textContent = `שגיאה: ${data.error || r.statusText}`;
      return;
    }
    renderTrainers(data.trainers);
    $("load-status").textContent = `נטענו ${data.trainers.length} מדריכות`;
  });

  $("btn-all").addEventListener("click", () => {
    trainersDiv.querySelectorAll("input[type=checkbox]").forEach(cb => cb.checked = true);
  });
  $("btn-none").addEventListener("click", () => {
    trainersDiv.querySelectorAll("input[type=checkbox]").forEach(cb => cb.checked = false);
  });

  $("btn-save-trainers").addEventListener("click", async () => {
    const enabled = Array.from(trainersDiv.querySelectorAll("input[type=checkbox]:checked"))
      .map(cb => cb.value);
    const r = await fetch("/api/trainers", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled_names: enabled}),
    });
    if (r.ok) alert("נשמר");
    else alert("שגיאה בשמירה");
  });

  $("btn-sync").addEventListener("click", async () => {
    $("sync-status").textContent = "מסנכרן...";
    const r = await fetch("/api/sync", {method: "POST"});
    const d = await r.json();
    if (r.status === 401) {
      $("sync-status").innerHTML = '<a href="/auth/google">צריך להתחבר מחדש ל-Google</a>';
      return;
    }
    if (r.ok) {
      $("sync-status").textContent = `✅ נוסף: ${d.added}, בוטל: ${d.removed}, שונה: ${d.changed}`;
    } else {
      $("sync-status").textContent = `❌ ${d.error || r.statusText}`;
    }
  });

  $("btn-apple").addEventListener("click", () => {
    const url = `${window.STUDIO.base_url}/feed/${window.STUDIO.feed_token}.ics`;
    $("apple-url").textContent = url;
    $("apple-link").classList.remove("hidden");
  });
  $("btn-copy").addEventListener("click", () => {
    const url = $("apple-url").textContent;
    navigator.clipboard.writeText(url);
    alert("הועתק!");
  });

  // Settings
  async function loadSettings() {
    const r = await fetch("/api/settings");
    const s = await r.json();
    $("urgent").value = s.urgent_window_hours;
    $("digest-time").value = s.digest_time;
    $("notif-cancel").checked = s.notify_on_cancellation;
    $("notif-change").checked = s.notify_on_change;
    $("notif-new").checked = s.notify_on_new_class;
  }
  loadSettings();

  $("btn-save-settings").addEventListener("click", async () => {
    const body = {
      urgent_window_hours: parseInt($("urgent").value),
      digest_time: $("digest-time").value,
      notify_on_cancellation: $("notif-cancel").checked,
      notify_on_change: $("notif-change").checked,
      notify_on_new_class: $("notif-new").checked,
    };
    const r = await fetch("/api/settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    if (r.ok) alert("נשמר");
    else alert("שגיאה");
  });
})();
