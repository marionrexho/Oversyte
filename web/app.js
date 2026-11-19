/* Oversyte dashboard: tiny hash-routed SPA, no build step. */
(() => {
  "use strict";

  // ---------- helpers ----------
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const app = $("#app");
  // static demo build (deploy/build_static_demo.py): data comes from demo.js, not a server
  const DEMO = !!window.OVERSYTE_DEMO;

  class AuthError extends Error {}

  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", headers: {}, ...opts };
    if (opts.body && typeof opts.body !== "string") {
      init.body = JSON.stringify(opts.body);
      init.headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, init);
    if (res.status === 401 && path !== "/api/login") {
      state.me.authed = false;
      const next = encodeURIComponent(location.pathname + location.hash || "/");
      location.replace(`/auth/login?next=${next}`);
      throw new AuthError("Sign in required");
    }
    const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
    if (!res.ok) throw new Error(data?.detail || `Request failed (${res.status})`);
    return data;
  }

  function toast(msg, opts = {}) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (opts.alert ? " alert" : "");
    t.hidden = false;
    t.onclick = () => { t.hidden = true; opts.onClick?.(); };
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), opts.ms || 3500);
  }

  function ago(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  const fullTime = (ts) => new Date(ts * 1000).toLocaleString(undefined,
    { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });

  const STATUS_LABEL = { new: "Needs review", theft: "Theft", false_alarm: "False alarm", dismissed: "Dismissed" };

  const svg = (paths) => `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
  const I = {
    alert: svg(`<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>`),
    check: svg(`<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>`),
    x: svg(`<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>`),
    eye: svg(`<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>`),
    download: svg(`<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>`),
    upload: svg(`<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>`),
    trash: svg(`<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>`),
    phone: svg(`<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>`),
    chart: svg(`<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>`),
    logout: svg(`<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>`),
    clock: svg(`<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>`),
    zap: svg(`<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>`),
    send: svg(`<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>`),
    play: `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 8 5.5z"/></svg>`,
    chevron: svg(`<path d="m9 18 6-6-6-6"/>`),
    lock: svg(`<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>`),
    store: svg(`<path d="M2 10l2-6h16l2 6z"/><path d="M4 10v10h16V10"/><path d="M10 20v-5h4v5"/>`),
    zone: svg(`<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><rect x="7" y="7" width="10" height="10" rx="1"/>`),
  };
  const ICON = {
    logo: `<img src="/icon.svg" alt="">`,
    bell: svg(`<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>`),
    cam: svg(`<path d="m16 13 5.2 3.1a.5.5 0 0 0 .8-.4V8.3a.5.5 0 0 0-.8-.4L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/>`),
    gear: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
    shield: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>`,
    back: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m15 18-6-6 6-6"/></svg>`,
  };

  // ---------- state ----------
  const state = {
    me: { authed: false, store_name: "" },
    filter: "",
    unreviewed: 0,
    newestTs: 0,
    cleanup: [],
  };

  function cleanupView() {
    state.cleanup.forEach((fn) => { try { fn(); } catch (_) {} });
    state.cleanup = [];
    // close MJPEG connections
    $$("img[data-live]").forEach((img) => (img.src = ""));
  }

  // ---------- shell ----------
  const brandHTML = () => `<div class="brand"><img src="/brand/oversyte-logo-sm.png" alt="Oversyte"></div>`;
  // Camera features only show when at least one camera is switched on in config.yaml.
  const hasCams = () => (state.cams ? state.cams.total : state.me.cameras || 0) > 0;
  // team mode = Oversyte staff view (Accuracy lab, labeling details); off for store owners
  const team = () => state.me.team_mode !== false;
  const liveChip = () => {
    if (!hasCams()) return `<span data-live-chip hidden></span>`;
    const { online = 0, total = 0 } = state.cams || {};
    const ok = total > 0 && online === total;
    const txt = !state.cams ? "Connecting…" : total === 0 ? "No cameras" : ok
      ? `${DEMO ? "Demo" : "Live"} · ${total} camera${total === 1 ? "" : "s"}` : `${online} of ${total} cameras online`;
    return `<span class="live-chip ${state.cams && !ok ? "off" : ""}" data-live-chip><span class="pulse"></span>${txt}</span>`;
  };
  const sideStatus = () => DEMO ? "Recorded demo" : !hasCams() ? (team() ? "Cameras off · labeling mode" : "Setting up · cameras not connected")
    : !state.cams ? "Protected by Oversyte" : `${state.cams.online}/${state.cams.total} cameras live`;
  const pageHead = (eyebrow, title, sub, right = "") => `
    <div class="page-head"><div>
      ${eyebrow ? `<div class="eyebrow">${eyebrow}</div>` : ""}<h1>${title}</h1>${sub ? `<p class="sub">${sub}</p>` : ""}
    </div>${right}</div>`;

  function shell(tab, html) {
    const badge = state.unreviewed ? `<span class="dot">${state.unreviewed > 99 ? "99+" : state.unreviewed}</span>` : "";
    const link = (t, href, icon, label, extra = "") =>
      `<a href="${href}" class="${tab === t ? "active" : ""}">${icon}<span>${label}</span>${extra}</a>`;
    app.innerHTML = `
      <aside class="sidebar">
        ${brandHTML()}
        <nav class="nav">
          ${link("alerts", "#/", ICON.bell, "Overview", badge)}
          ${hasCams() ? link("cameras", "#/cameras", ICON.cam, "Cameras") : ""}
          ${team() ? link("lab", "#/lab", I.chart, "Accuracy") : ""}
          ${link("settings", "#/settings", ICON.gear, "Settings")}
        </nav>
        <div class="side-foot"><div class="store-card"><span class="pulse"></span>
          <div class="grow"><b>${esc(state.me.store_name)}</b><small id="side-status">${sideStatus()}</small></div></div></div>
      </aside>
      <header class="topbar">${brandHTML(false)}${liveChip()}</header>
      <main id="view">${html}</main>`;
    return $("#view");
  }

  // ---------- login ----------
  function renderLogin() {
    const next = encodeURIComponent(location.pathname + location.hash || "/");
    location.replace(`/auth/login?next=${next}`);
  }

  // ---------- alerts ----------
  const meter = (score) => {
    const p = Math.round(score * 100);
    return `<div class="conf">Confidence <div class="meter ${score >= 0.75 ? "high" : score >= 0.6 ? "mid" : ""}"><i style="width:${p}%"></i></div><b>${p}%</b></div>`;
  };
  const statusPill = (ev) => ev.is_test ? `<span class="pill test">Test</span>`
    : `<span class="pill ${esc(ev.status)}">${esc(STATUS_LABEL[ev.status])}</span>`;


  const greeting = () => {
    const h = new Date().getHours();
    return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  };

  // ----- overview pieces -----
  const shortDay = (iso) => new Date(iso + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

  function metricsHTML(st) {
    const reviewed = st.theft_week + st.false_alarm_week;
    const m = (value, label, hot = false) => `<div class="metric${hot ? " hot" : ""}" ${hot ? `data-f="new" role="button"` : ""}><b>${value}</b><span>${label}</span></div>`;
    return m(st.today, "Alerts today")
      + m(st.unreviewed, st.unreviewed ? `Needs review ${I.chevron}` : "Needs review", st.unreviewed > 0)
      + m(st.theft_week, "Thefts (7 days)")
      + m(reviewed ? `${Math.round((st.false_alarm_week / reviewed) * 100)}%` : "–", "False alarm rate");
  }

  /** 14-day alert bars: one series, recessive grid, per-bar hover tooltip. */
  function activityChart(daily) {
    const W = 420, H = 190, L = 22, R = 4, T = 10, B = 26;
    const max = Math.max(...daily.map((d) => d.alerts));
    const top = max <= 4 ? 4 : Math.ceil(max / 4) * 4;
    const slot = (W - L - R) / daily.length, bw = Math.min(14, slot * 0.55);
    const y = (v) => T + (H - T - B) * (1 - v / top);
    const today = daily.at(-1).date;
    let s = `<svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img" aria-label="Alerts per day, last 14 days">`;
    [0, top].forEach((t) => {
      s += `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" class="grid${t === 0 ? " base" : ""}"/>`
        + `<text x="${L - 6}" y="${y(t) + 4}" text-anchor="end" class="tick">${t}</text>`;
    });
    daily.forEach((d, i) => {
      const cx = L + slot * i + slot / 2, x = cx - bw / 2, y0 = y(0);
      if (d.alerts > 0) {
        const yt = y(d.alerts), r = Math.min(3, (y0 - yt) / 2);
        s += `<path class="bar" d="M${x} ${y0}V${yt + r}Q${x} ${yt} ${x + r} ${yt}H${x + bw - r}Q${x + bw} ${yt} ${x + bw} ${yt + r}V${y0}Z"/>`;
      }
      if (i === 0 || i === daily.length - 1 || i === Math.floor(daily.length / 2)) {
        s += `<text x="${cx}" y="${H - 6}" text-anchor="middle" class="tick">${d.date === today ? "Today" : shortDay(d.date)}</text>`;
      }
      s += `<rect class="hit" data-i="${i}" x="${L + slot * i}" y="${T}" width="${slot}" height="${H - T - B}"/>`;
    });
    return s + "</svg>";
  }

  /** Setup steps, in order. ok = done; act = link, "test", or null (done by the installer). */
  function setupSteps(o) {
    const s = [
      { ok: o.cameras.total > 0, title: "Connect cameras", act: null },
      { ok: o.zones > 0, title: "Mark shelves", act: o.cameras.total ? "#/cameras" : null, btn: "Mark shelves" },
      { ok: o.devices > 0, title: "Phone alerts", act: "#/settings", btn: "Set up phone alerts" },
      { ok: o.has_test_alert, title: "Test alert", act: "test", btn: "Send a test alert" },
    ];
    if (team()) s.push({ ok: o.accuracy.catch_rate != null, title: "Measure accuracy", act: "#/lab", btn: "Open Accuracy lab" });
    return s;
  }

  /** One slim line that says whether the store is protected, or what to do next. */
  function statusStrip(o) {
    const c = o.cameras, plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
    if (c.total && c.online === c.total) {
      return `<div class="strip"><span class="sdot ok"></span><b>Protected</b>
        <span class="muted">Watching ${plural(c.total, "camera")} · ${o.devices ? `${plural(o.devices, "phone")} get alerts` : "no phones set up yet"}</span>
        <span class="grow"></span><a class="strip-link" href="#/cameras">View cameras ${I.chevron}</a></div>`;
    }
    if (c.total) {
      return `<div class="strip warn"><span class="sdot warn"></span><b>${plural(c.total - c.online, "camera")} offline</b>
        <span class="muted">Oversyte can't watch those areas until they reconnect</span>
        <span class="grow"></span><a class="strip-link" href="#/cameras">Check cameras ${I.chevron}</a></div>`;
    }
    const steps = setupSteps(o), done = steps.filter((s) => s.ok).length;
    const next = steps.find((s) => !s.ok && s.act);
    const btn = !next ? "" : next.act === "test"
      ? `<button class="btn small primary" type="button" data-test>${next.btn}</button>`
      : `<a class="btn small primary" href="${next.act}">${next.btn}</a>`;
    return `<div class="strip setup">
      <div class="strip-row"><span class="sdot"></span><b>Finish setup</b><span class="muted">${done} of ${steps.length} done</span><span class="grow"></span>${btn}</div>
      <div class="strip-bar"><i style="width:${(done / steps.length) * 100}%"></i></div>
      <div class="strip-steps">${steps.map((s) => `<span class="${s.ok ? "done" : ""}">${s.ok ? I.check : "<i></i>"}${s.title}</span>`).join("")}</div>
    </div>`;
  }

  function alertRow(ev) {
    const img = ev.thumb_url ? `style="background-image:url('${esc(ev.thumb_url)}')"` : "";
    return `<a class="arow" href="#/event/${ev.id}">
      <span class="athumb ${ev.thumb_url ? "" : "noimg"}" ${img}>${ev.thumb_url ? "" : ICON.cam}</span>
      <span class="grow"><span class="arow-top"><b>${esc(ev.camera_name)}</b>${statusPill(ev)}</span>
        <span class="arow-reason">${esc(ev.reason)}</span></span>
      <span class="arow-meta"><span>${ago(ev.ts)}</span><small>${Math.round(ev.score * 100)}%</small></span>
    </a>`;
  }

  async function alertsView() {
    const now = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
    const view = shell("alerts", `
      <div class="ov-head">
        <div><p class="ov-date">${greeting()} · ${now}</p><h1>Overview</h1></div>
        <button class="btn small" type="button" data-test aria-label="Send test alert">${I.send}<span class="lbl">Send test alert</span></button>
      </div>

      <div id="strip"></div>
      <div class="metrics" id="metrics">${metricsHTML({ today: "–", unreviewed: 0, theft_week: "–", false_alarm_week: 0 })}</div>
      <div class="ov2">
        <section class="panel">
          <div class="panel-head"><h2>Recent alerts</h2>
            <div class="tabs" id="chips">${[["", "All"], ["new", "To review"], ["theft", "Theft"], ["false_alarm", "False alarm"]]
              .map(([v, l]) => `<button class="chip ${state.filter === v ? "active" : ""}" data-f="${v}">${l}</button>`).join("")}</div>
          </div>
          <div class="alist" id="events"><div class="spinner"></div></div>
          <button class="more" id="more" hidden>Show older</button>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>Activity</h2><span class="muted small"><b id="total14">–</b> alerts · 14 days</span></div>
          <div class="chart-wrap" id="chart"><div class="spinner"></div></div>
        </section>
      </div>`);

    let oldest = null;
    const list = $("#events", view);
    let daily = [];

    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    $("#chart", view).appendChild(tip);
    $("#chart", view).addEventListener("mousemove", (e) => {
      const hit = e.target.closest(".hit");
      $$(".bar.on", view).forEach((b) => b.classList.remove("on"));
      if (!hit) { tip.hidden = true; return; }
      const d = daily[+hit.dataset.i];
      $$(".bar", view)[daily.slice(0, +hit.dataset.i).filter((x) => x.alerts > 0).length]?.classList.toggle("on", d.alerts > 0);
      tip.innerHTML = `<b>${d.date === daily.at(-1).date ? "Today" : shortDay(d.date)}</b>
        <div><span>Alerts</span><b>${d.alerts}</b></div><div><span>Theft</span><b>${d.theft}</b></div>
        <div><span>False alarm</span><b>${d.false_alarm}</b></div><div><span>To review</span><b>${d.open}</b></div>`;
      const box = $("#chart", view).getBoundingClientRect(), hb = hit.getBoundingClientRect();
      tip.hidden = false;
      tip.style.left = `${Math.min(Math.max(hb.left - box.left + hb.width / 2 - tip.offsetWidth / 2, 0), box.width - tip.offsetWidth)}px`;
      tip.style.top = `${Math.max(hb.top - box.top - 8, 0)}px`;
    });
    $("#chart", view).addEventListener("mouseleave", () => { tip.hidden = true; $$(".bar.on", view).forEach((b) => b.classList.remove("on")); });

    const loadOverview = async () => {
      const o = await api("/api/overview");
      daily = o.daily;
      $("#metrics", view).innerHTML = metricsHTML(o.stats);
      $("#strip", view).innerHTML = statusStrip(o);
      const total = daily.reduce((n, d) => n + d.alerts, 0);
      $("#total14", view).textContent = total;
      $$("#chart > :not(.chart-tip)", view).forEach((n) => n.remove());
      $("#chart", view).insertAdjacentHTML("afterbegin", activityChart(daily)
        + (total ? "" : `<div class="chart-empty">No alerts in the last 14 days</div>`));
      $("#chart", view).classList.toggle("is-empty", !total);
    };

    const load = async (append = false) => {
      const q = new URLSearchParams({ limit: 20 });
      if (state.filter) q.set("status", state.filter);
      if (append && oldest) q.set("before", oldest);
      const events = await api("/api/events?" + q);
      if (!append) loadOverview().catch(() => {});
      if (!append && !events.length) {
        list.innerHTML = `<div class="alist-empty">${ICON.shield}<b>${state.filter ? "Nothing here" : "No alerts yet"}</b>
          <span>${state.filter ? "No alerts match this filter." : "When someone conceals an item, it shows up here and on your team's phones."}</span></div>`;
      } else {
        const html = events.map(alertRow).join("");
        if (append) list.insertAdjacentHTML("beforeend", html); else list.innerHTML = html;
      }
      if (events.length) oldest = events[events.length - 1].ts;
      $("#more", view).hidden = events.length < 20;
    };

    const setFilter = (f) => {
      state.filter = f;
      $$(".chip", view).forEach((c) => c.classList.toggle("active", c.dataset.f === f));
      oldest = null;
      load().catch(handleErr);
    };
    $("#chips", view).onclick = (e) => { const b = e.target.closest(".chip"); if (b) setFilter(b.dataset.f); };
    view.addEventListener("click", (e) => {
      const t = e.target.closest("[data-test]");
      if (t) { sendTest(null, t).then(() => setTimeout(() => load().catch(() => {}), 500)); return; }
      const m = e.target.closest(".metric[data-f]");
      if (m) setFilter(m.dataset.f);
    });
    $("#more", view).onclick = () => load(true).catch(handleErr);
    await load();
    const t = setInterval(() => load().catch(() => {}), 8000);
    state.cleanup.push(() => clearInterval(t));
  }

  // ---------- incident ----------
  async function eventView(id) {
    const view = shell("alerts", `<div class="empty"><div class="spinner" style="margin:auto"></div></div>`);
    const ev = await api(`/api/events/${encodeURIComponent(id)}`);
    const media = ev.clip_url
      ? `<video src="${esc(ev.clip_url)}" controls autoplay muted loop playsinline ${ev.thumb_url ? `poster="${esc(ev.thumb_url)}"` : ""}></video>`
      : ev.thumb_url ? `<img src="${esc(ev.thumb_url)}" alt="">` : `<span class="muted small">No video for this alert</span>`;
    const REGION = { pocket: "Pocket", bag: "Bag", waistband: "Waistband / clothing" };
    view.innerHTML = `
      <a class="back" href="#/">${ICON.back}All alerts</a>
      <div class="detail">
        <div class="stack">
          <div class="player">${media}</div>
          <div class="row small muted">${I.clock}<span>${fullTime(ev.ts)}</span><span class="grow"></span>
            ${ev.clip_url ? `<a class="btn small" href="${esc(ev.clip_url)}" download="oversyte-${ev.id}.mp4">${I.download}Download clip</a>` : ""}</div>
        </div>
        <div class="stack">
          <div class="card stack">
            <div class="detail-title"><div class="grow">
                <div class="eyebrow" style="margin-bottom:6px">${ev.is_test ? "Test alert" : "Possible theft"}</div>
                <h1>${esc(ev.camera_name)}</h1></div>
              ${ev.is_test ? `<span class="pill test">Test</span>` : `<span class="pill ${esc(ev.status)}" id="status-pill">${esc(STATUS_LABEL[ev.status])}</span>`}</div>
            <p class="saw">${esc(ev.reason)}</p>
            ${meter(ev.score)}
            <div class="facts">
              <div class="fact"><small>When</small><span>${ago(ev.ts)}</span></div>
              <div class="fact"><small>Concealed in</small><span>${esc(REGION[ev.region] || "–")}</span></div>
            </div>
          </div>
          <div class="card stack">
            <div><h2>What happened?</h2>
              <p class="small muted" style="margin-top:4px">Watch the clip, then mark it. This also teaches Oversyte what to flag in your store.</p></div>
            <div class="actions">
              <button class="btn danger" data-s="theft"></button>
              <button class="btn" data-s="false_alarm"></button>
            </div>
            <label class="field"><span>Notes</span>
              <textarea id="notes" placeholder="What happened, what staff did…">${esc(ev.notes)}</textarea></label>
            <div class="row"><span class="grow"></span><button class="linkbtn danger small" id="del">${I.trash}Delete alert</button></div>
          </div>
        </div>
      </div>`;

    const paint = (status) => {
      $$(".actions .btn", view).forEach((b) => {
        const on = b.dataset.s === status;
        b.classList.toggle("chosen", on);
        b.innerHTML = { theft: on ? `${I.check}Theft confirmed` : `${I.alert}Confirm theft`,
          false_alarm: on ? `${I.check}False alarm` : `${I.x}False alarm` }[b.dataset.s];
      });
      const pill = $("#status-pill", view);
      if (pill) { pill.className = `pill ${status}`; pill.textContent = STATUS_LABEL[status]; }
    };
    paint(ev.status);

    $(".actions", view).onclick = async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      const status = ev.status === b.dataset.s ? "new" : b.dataset.s;
      const upd = await api(`/api/events/${ev.id}`, { method: "PATCH", body: { status } });
      ev.status = upd.status;
      paint(ev.status);
      refreshBadge();
      toast(status === "new" ? "Marked as needs review" : `Saved: ${STATUS_LABEL[status]}`);
    };
    $("#notes", view).onchange = async (e) => {
      await api(`/api/events/${ev.id}`, { method: "PATCH", body: { notes: e.target.value } });
      toast("Notes saved");
    };
    $("#del", view).onclick = async () => {
      if (!confirm("Delete this alert and its clip?")) return;
      await api(`/api/events/${ev.id}`, { method: "DELETE" });
      location.hash = "#/";
    };
  }

  // ---------- cameras ----------
  async function camerasView() {
    const view = shell("cameras", pageHead("Monitor", "Cameras",
      DEMO ? "Recorded store footage with Oversyte's AI overlay, on a loop." : "Live feeds with AI overlay. Open a camera to draw shelf zones.",
      `<div class="row">${liveChip()}</div>`)
      + `<div class="cams" id="cams"></div>`);
    const cams = await api("/api/cameras");
    const grid = $("#cams", view);
    if (!cams.length) {
      grid.outerHTML = `<div class="card card-head"><span class="ico-tile">${ICON.cam}</span><div class="grow"><h2>No cameras yet</h2>
        <p>Add your cameras to <b>config.yaml</b> under <code>cameras:</code> (RTSP address, webcam number or a video file) and restart Oversyte.</p></div></div>`;
      return;
    }
    grid.innerHTML = cams.map((c) => `
      <a class="cam" href="#/camera/${encodeURIComponent(c.id)}">
        <div class="view">
          ${c.video_url ? feedVideo(c) : c.connected ? `<img data-live src="/api/cameras/${encodeURIComponent(c.id)}/live" alt="">` : `<span>${esc(c.error || "Connecting…")}</span>`}
          <span class="live-badge ${c.connected ? "" : "offline"}"><span class="pulse"></span>${DEMO ? "DEMO FEED" : c.connected ? "LIVE" : "OFFLINE"}</span>
          ${c.connected && c.ai_ready ? `<span class="tr"><span class="pill glass">${c.people} ${c.people === 1 ? "person" : "people"}</span></span>` : ""}
          <div class="overlay-meta"><div class="grow"><b>${esc(c.name)}</b><small>${camLine(c)}</small></div>${I.chevron}</div>
        </div>
      </a>`).join("");
  }

  function camLine(c) {
    if (DEMO) return "Recorded demo footage · AI overlay";
    if (!c.connected) return esc(c.error || "Offline");
    if (c.error) return esc(c.error);
    if (!c.ai_ready) return "Loading AI…";
    return `AI active · ${c.analysis_ms} ms per frame`;
  }
  // static demo: a recorded camera loop with the AI's boxes drawn in, instead of a live stream
  const feedVideo = (c, id = "") => `<video ${id ? `id="${id}"` : ""} src="${esc(c.video_url)}" autoplay muted loop playsinline></video>`;

  async function cameraView(id) {
    const enc = encodeURIComponent(id);
    const cams = await api("/api/cameras");
    const cam = cams.find((c) => c.id === id);
    if (!cam) { location.hash = "#/cameras"; return; }
    const view = shell("cameras", `
      <a class="back" href="#/cameras">${ICON.back}All cameras</a>
      ${pageHead(DEMO ? "Camera" : "Live camera", esc(cam.name), `<span id="camline">${camLine(cam)}</span>`,
        DEMO ? "" : `<button class="btn" id="edit">${I.zone}Edit zones</button>`)}
      <div class="stack">
        <div class="live-wrap" id="wrap">${cam.video_url ? feedVideo(cam, "live") : `<img data-live id="live" src="/api/cameras/${enc}/live" alt="">`}
          <span class="live-badge ${cam.connected ? "" : "offline"}"><span class="pulse"></span>${DEMO ? "DEMO FEED" : cam.connected ? "LIVE" : "OFFLINE"}</span></div>
        <div id="panel"></div>
      </div>`);

    const legendHTML = `<div class="card stack">
      <div class="card-head"><span class="ico-tile violet">${I.zone}</span><div class="grow"><h2>How Oversyte reads this camera</h2>
        <p>Colors in the live view show what the AI is tracking.</p></div></div>
      <div class="legend">
        <span><i style="background:#78c850"></i>Shelf zone</span><span><i style="background:#969696"></i>Ignore zone</span>
        <span><i style="background:#c8c8c8"></i>Idle</span><span><i style="background:#ffc800"></i>Reaching</span>
        <span><i style="background:#ff8c00"></i>Holding item</span><span><i style="background:#eb2828"></i>Concealing</span>
      </div>
      <p class="small muted" style="margin:0"><b>Shelf zones</b> tell Oversyte where products are. Draw one over each shelf face this camera sees.
        Without shelf zones, any outstretched arm counts as a reach, which gives more false alarms.
        <b>Ignore zones</b> cover areas like behind the counter, so staff aren't flagged.</p>
      <div><button class="btn small" id="test">${I.send}Send a test alert from this camera</button></div></div>`;
    const panel = $("#panel", view);
    panel.innerHTML = legendHTML;
    const bindTest = () => {
      const tb = $("#test", panel);
      if (tb) tb.onclick = () => sendTest(id, tb);
    };
    bindTest();

    const t = setInterval(async () => {
      try {
        const c = (await api("/api/cameras")).find((x) => x.id === id);
        if (c) $("#camline", view).textContent = camLine(c).replace(/<[^>]+>/g, "");
      } catch (_) {}
    }, 3000);
    state.cleanup.push(() => clearInterval(t));

    $("#edit", view)?.addEventListener("click", () => zoneEditor(id, view, () => { panel.innerHTML = legendHTML; bindTest(); }));
  }

  async function zoneEditor(id, view, onDone) {
    const enc = encodeURIComponent(id);
    const wrap = $("#wrap", view);
    const live = $("#live", view);
    const panel = $("#panel", view);
    const editBtn = $("#edit", view);
    editBtn.hidden = true;
    const zones = await api(`/api/cameras/${enc}/zones`);
    live.src = "";
    live.removeAttribute("data-live");
    live.src = `/api/cameras/${enc}/snapshot.jpg?t=${Date.now()}`;
    await new Promise((r) => { live.onload = r; live.onerror = r; });
    zoneDrawer({
      wrap, media: live, panel, zones,
      onSave: async (z) => {
        await api(`/api/cameras/${enc}/zones`, { method: "PUT", body: z });
        toast("Zones saved and active now");
      },
      onExit: () => {
        live.setAttribute("data-live", "");
        live.src = `/api/cameras/${enc}/live`;
        editBtn.hidden = false;
        onDone();
      },
    });
  }

  const ZONE_COLORS = { shelf: "#78c850", ignore: "#969696" };

  function paintZones(canvas, media, zones, pts = [], kind = "shelf") {
    const r = media.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = r.width * dpr; canvas.height = r.height * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const W = r.width, H = r.height;
    const poly = (p, color, closed) => {
      if (!p.length) return;
      ctx.beginPath();
      p.forEach(([x, y], i) => (i ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H)));
      if (closed) { ctx.closePath(); ctx.fillStyle = color + "40"; ctx.fill(); }
      ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.stroke();
      p.forEach(([x, y], i) => { ctx.beginPath(); ctx.arc(x * W, y * H, i === 0 && !closed ? 7 : 4, 0, 7); ctx.fillStyle = color; ctx.fill(); });
    };
    for (const k of ["shelf", "ignore"]) (zones[k] || []).forEach((p) => poly(p, ZONE_COLORS[k], true));
    poly(pts, ZONE_COLORS[kind], false);
  }

  /**
   * Zoom + pan for a viewport whose child `inner` holds the media (and any canvases).
   * Wheel / pinch / buttons zoom around the pointer; drag pans. A click that ends a
   * drag is swallowed, so panning never toggles play or drops a zone corner.
   */
  function makeZoomable(viewport, inner, onChange) {
    let z = 1, tx = 0, ty = 0;
    const MAX = 8;
    const clamp = () => {
      const W = viewport.clientWidth, H = viewport.clientHeight;
      tx = Math.min(0, Math.max(W - W * z, tx));
      ty = Math.min(0, Math.max(H - H * z, ty));
    };
    const apply = () => {
      clamp();
      inner.style.transform = `translate(${tx}px, ${ty}px) scale(${z})`;
      viewport.classList.toggle("zoomed", z > 1.001);
      onChange?.(z);
      window.dispatchEvent(new Event("resize")); // redraw zone canvases at the new sharpness
    };
    const zoomAt = (factor, px, py) => {
      const nz = Math.min(MAX, Math.max(1, z * factor));
      const cx = (px - tx) / z, cy = (py - ty) / z; // content point under the pointer
      z = nz; tx = px - cx * z; ty = py - cy * z;
      apply();
    };
    const local = (e) => {
      const r = viewport.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    };

    viewport.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [px, py] = local(e);
      const dy = Math.max(-120, Math.min(120, e.deltaY)); // one mouse-wheel notch ≈ 1.2× zoom
      zoomAt(Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0018)), px, py); // ctrlKey = touchpad pinch
    }, { passive: false });

    const pts = new Map();
    let drag = null, pinch = null, swallowClick = false;
    viewport.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".zoom-ctl")) return;
      pts.set(e.pointerId, local(e));
      if (pts.size === 1) drag = { start: local(e), tx, ty, moved: false };
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z };
        drag = null;
      }
    });
    viewport.addEventListener("pointermove", (e) => {
      if (!pts.has(e.pointerId)) return;
      pts.set(e.pointerId, local(e));
      if (pinch && pts.size === 2) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        zoomAt((pinch.z * d) / pinch.d / z, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
        swallowClick = true;
        return;
      }
      if (!drag) return;
      const [x, y] = local(e);
      if (!drag.moved && Math.hypot(x - drag.start[0], y - drag.start[1]) < 5) return;
      if (z <= 1.001) return; // nothing to pan at 100%
      drag.moved = swallowClick = true;
      tx = drag.tx + (x - drag.start[0]);
      ty = drag.ty + (y - drag.start[1]);
      apply();
    });
    const end = (e) => {
      pts.delete(e.pointerId);
      if (pts.size < 2) pinch = null;
      if (!pts.size) drag = null;
    };
    viewport.addEventListener("pointerup", end);
    viewport.addEventListener("pointercancel", end);
    viewport.addEventListener("click", (e) => {
      if (swallowClick) { e.stopPropagation(); e.preventDefault(); swallowClick = false; }
    }, true);

    const center = () => [viewport.clientWidth / 2, viewport.clientHeight / 2];
    return {
      by: (f) => zoomAt(f, ...center()),
      reset: () => { z = 1; tx = ty = 0; apply(); },
    };
  }

  /** Lets the user draw shelf/ignore polygons over an <img> or paused <video>. */
  function zoneDrawer({ wrap, media, panel, zones, onSave, onExit }) {
    zones = { shelf: [...(zones.shelf || [])], ignore: [...(zones.ignore || [])] };
    let kind = "shelf";
    let pts = [];
    const canvas = document.createElement("canvas");
    canvas.className = "draw";
    wrap.appendChild(canvas);

    panel.innerHTML = `<div class="card stack">
      <div class="row"><div class="seg" id="kind"><button data-k="shelf" class="active">Shelf zone</button><button data-k="ignore">Ignore zone</button></div></div>
      <p class="small muted" style="margin:0">Tap the picture to place corners around a shelf. Tap the first corner again (or press <b>Close shape</b>) to finish. Add as many shapes as you need.</p>
      <div class="row">
        <button class="btn small" id="close">Close shape</button>
        <button class="btn small ghost" id="undo">Undo</button>
        <button class="btn small ghost" id="poplast">Remove last shape</button>
        <button class="btn small ghost" id="clear">Clear all</button>
      </div>
      <div class="row"><button class="btn primary grow" id="save">Save zones</button><button class="btn" id="cancel">Cancel</button></div>
    </div>`;

    const draw = () => paintZones(canvas, media, zones, pts, kind);
    const finish = () => {
      if (pts.length < 3) { toast("A shape needs at least 3 corners"); return; }
      zones[kind].push(pts); pts = []; draw();
    };
    canvas.onclick = (e) => {
      const r = canvas.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      if (pts.length >= 3) {
        const [fx, fy] = pts[0];
        if (Math.hypot((fx - x) * r.width, (fy - y) * r.height) < 18) { finish(); return; }
      }
      pts.push([+x.toFixed(4), +y.toFixed(4)]); draw();
    };
    $("#kind", panel).onclick = (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (pts.length) finish();
      kind = b.dataset.k;
      $$("#kind button", panel).forEach((x) => x.classList.toggle("active", x === b)); draw();
    };
    $("#close", panel).onclick = finish;
    $("#undo", panel).onclick = () => { pts.pop(); draw(); };
    $("#poplast", panel).onclick = () => { zones[kind].pop(); draw(); };
    $("#clear", panel).onclick = () => { if (confirm("Remove all zones?")) { zones = { shelf: [], ignore: [] }; pts = []; draw(); } };
    const exit = () => {
      window.removeEventListener("resize", draw);
      canvas.remove();
      onExit();
    };
    $("#save", panel).onclick = async () => {
      if (pts.length >= 3) finish();
      try { await onSave(zones); exit(); } catch (e) { handleErr(e); }
    };
    $("#cancel", panel).onclick = exit;
    window.addEventListener("resize", draw);
    state.cleanup.push(() => window.removeEventListener("resize", draw));
    draw();
  }

  async function sendTest(cameraId, btn) {
    btn.disabled = true;
    const label = btn.innerHTML;
    btn.textContent = "Sending…";
    try {
      const ev = await api("/api/test-alert", { method: "POST", body: { camera_id: cameraId || null } });
      toast("Test alert sent. Check your phone.", { onClick: () => (location.hash = `#/event/${ev.id}`) });
    } catch (e) { handleErr(e); }
    finally { btn.disabled = false; btn.innerHTML = label; }
  }

  // ---------- accuracy lab ----------
  const fmtT = (s) => {
    if (s == null || isNaN(s)) return "–";
    const m = Math.floor(s / 60), r = s - m * 60;
    return `${m}:${r.toFixed(1).padStart(4, "0")}`;
  };
  const fmtDur = (s) => (s == null ? "?" : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : `${Math.round(s / 60) || "<1"} min`);

  const QUALITY = { good: "Good quality", fair: "Fair quality", poor: "Poor quality" };
  const myName = () => { try { return localStorage.getItem("oversyte-labeler") || ""; } catch (_) { return ""; } };

  async function labView() {
    const view = shell("lab", pageHead("Improve", "Accuracy lab",
      "Label real store footage, then measure exactly how many thefts Oversyte catches.")
      + `<div class="stack" id="lab"><div class="spinner"></div></div>`);
    const data = await api("/api/lab");
    const r = data.results;
    const s = r?.summary;
    const done = data.videos.filter((v) => v.done).length;
    const pctS = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);

    $("#lab", view).innerHTML = `
      ${s ? `<div class="card stack">
        <div class="card-head"><span class="ico-tile violet">${I.chart}</span><div class="grow"><h2>Latest accuracy score</h2>
          <p>${ago(r.run_at)} · alert threshold ${r.threshold}${r.setup ? ` · ${esc(r.setup)}` : ""}</p></div></div>
        <div class="stats">
          <div class="stat"><span class="ico-tile green">${I.check}</span><b>${pctS(s.catch_rate)}</b><span>Catch rate (${s.caught}/${s.events} thefts)</span></div>
          <div class="stat ${s.false_alarms ? "hot" : ""}"><span class="ico-tile red">${I.alert}</span><b>${s.false_alarms_per_hour ?? "–"}</b><span>False alarms per hour</span></div>
          <div class="stat"><span class="ico-tile">${I.eye}</span><b>${pctS(s.precision)}</b><span>Alerts that were real</span></div>
          <div class="stat"><span class="ico-tile amber">${ICON.cam}</span><b>${s.videos}</b><span>Videos · ${fmtDur(s.hours * 3600)}</span></div>
        </div>
        <details><summary class="small muted" style="cursor:pointer">What if the alert threshold changed?</summary>
          <table class="tbl"><tr><th>Threshold</th><th>Catch rate</th><th>False alarms</th><th>Real alerts</th></tr>
          ${r.sweep.map((x) => `<tr class="${Math.abs(x.threshold - r.threshold) < 1e-9 ? "cur" : ""}"><td>${x.threshold.toFixed(2)}</td><td>${pctS(x.catch_rate)}</td><td>${x.false_alarms}</td><td>${pctS(x.precision)}</td></tr>`).join("")}
          </table></details>
      </div>` : ""}
      <div class="card stack">
        <div class="card-head"><span class="ico-tile">${ICON.cam}</span><div class="grow"><h2>Test videos</h2>
          <p>${done} of ${data.videos.length} labeled</p></div>
          <label class="btn small primary">${I.upload}Upload<input type="file" id="up" accept="video/*" multiple hidden></label></div>
        <div id="upmsg" class="small muted" hidden></div>
        ${data.videos.length ? `<div class="list">${data.videos.map((v) => `
          <a class="item" href="#/lab/${encodeURIComponent(v.name)}" style="color:inherit">
            <div class="grow" style="min-width:0"><b style="word-break:break-all">${esc(v.name)}</b>
              <div class="small muted">${fmtDur(v.duration)} · ${v.events} theft${v.events === 1 ? "" : "s"} marked · ${v.zones ? `${v.zones} shelf zone${v.zones === 1 ? "" : "s"}` : "no shelf zones"}${v.angles > 1 ? ` · ${v.angles} camera angles` : ""}${v.labeled_by ? ` · by ${esc(v.labeled_by)}` : ""}
              ${v.result ? ` · <span style="color:var(--text)">caught ${v.result.caught}/${v.result.events}, ${v.result.false_alarms} false</span>` : ""}</div>
              ${v.quality ? `<div class="small" style="margin-top:3px"><span class="q ${v.quality.grade}">${QUALITY[v.quality.grade]}</span>
                <span class="muted">${esc(v.quality.issues.join(", "))}</span></div>` : ""}</div>
            <span class="pill ${v.done ? "false_alarm" : v.events || v.zones ? "test" : ""}">${v.done ? "Labeled" : v.events || v.zones ? "In progress" : "To do"}</span>
          </a>`).join("")}</div>`
        : `<p class="small muted" style="margin:0">No videos yet. Upload some, or copy them into:<br><code style="word-break:break-all">${esc(data.folder)}</code></p>`}
      </div>
      <div class="card stack">
        <div class="card-head"><span class="ico-tile green">${I.zap}</span><div class="grow"><h2>Get your score</h2></div></div>
        <p class="small muted" style="margin:0">Run these in the Oversyte folder (stop the dashboard first if your computer is slow).</p>
        <p class="small" style="margin:0"><b>1. Before labeling:</b> rate every video's quality. This takes about as long as the videos themselves, and makes step 2 instant.</p>
        <pre class="code">.venv\\Scripts\\python eval.py --check</pre>
        <p class="small" style="margin:0"><b>2. After labeling:</b> get the score.</p>
        <pre class="code">.venv\\Scripts\\python eval.py</pre>
      </div>`;

    $("#up", view).onchange = async (e) => {
      const files = [...e.target.files];
      const msg = $("#upmsg", view);
      msg.hidden = false;
      for (const [i, f] of files.entries()) {
        await new Promise((res, rej) => {
          const fd = new FormData();
          fd.append("file", f);
          const x = new XMLHttpRequest();
          x.open("POST", "/api/lab/upload");
          x.upload.onprogress = (p) => (msg.textContent = `Uploading ${i + 1}/${files.length}: ${f.name} ${Math.round((p.loaded / p.total) * 100)}%`);
          x.onload = () => (x.status < 300 ? res() : rej(new Error(JSON.parse(x.responseText || "{}").detail || "Upload failed")));
          x.onerror = () => rej(new Error("Upload failed"));
          x.send(fd);
        }).catch(handleErr);
      }
      labView();
    };
  }

  async function labelView(name) {
    const enc = encodeURIComponent(name);
    const view = shell("lab", `<div class="spinner"></div>`);
    const [{ labels, result, quality: q }, meta] = await Promise.all([api(`/api/lab/${enc}/labels`), api("/api/lab")]);
    const TYPES = meta.types;
    const typeKeys = Object.keys(TYPES);
    let lb = labels;

    view.innerHTML = `
      <a class="back" href="#/lab">${ICON.back}Accuracy lab</a>
      <div class="stack">
        <h2 style="word-break:break-all">${esc(name)}</h2>
        ${q ? `<div class="small"><span class="q ${q.grade}">${QUALITY[q.grade]}</span>
          <span class="muted">${esc(q.resolution)}${q.fps ? ` · ${q.fps} fps` : ""}${q.person_px ? ` · people ~${q.person_px}px` : ""}${q.hands_visible != null ? ` · hands visible ${Math.round(q.hands_visible * 100)}%` : ""}${q.issues.length ? ` · ${esc(q.issues.join(", "))}` : ""}</span></div>` : ""}
        <div class="live-wrap zoomable" id="vwrap">
          <div class="zoom-inner" id="zin"><video id="vid" preload="auto" playsinline></video><canvas class="overlay" id="zov"></canvas></div>
          <div class="vmsg" id="vmsg"><div class="spinner"></div><span>Loading video…</span></div>
          <div class="zoom-ctl" id="zctl">
            <button data-z="out" title="Zoom out (−)">−</button><span id="zlevel">100%</span><button data-z="in" title="Zoom in (+)">+</button>
            <button data-z="reset" id="zreset" title="Reset zoom (0)" hidden>Reset</button>
          </div>
        </div>
        <div class="small muted" style="margin-top:-6px">Zoom: mouse wheel, pinch, or <span class="kbd">+</span><span class="kbd">−</span>. Drag to move around. <span class="kbd">0</span> resets.</div>
        <div class="timeline" id="tl"><div class="played" id="played"></div><div class="head" id="head"></div></div>
        <div class="row controls">
          <button class="btn small" data-j="-2" title="Back 2s (←)">−2s</button>
          <button class="btn small" data-j="-0.2" title="Back a little (Shift+←)">−⅕</button>
          <button class="btn small primary" id="play" title="Play / pause (Space)">Play</button>
          <button class="btn small" data-j="0.2" title="Forward a little (Shift+→)">+⅕</button>
          <button class="btn small" data-j="2" title="Forward 2s (→)">+2s</button>
          <span class="mono" id="now">0:00.0</span>
          <span class="grow"></span>
          <div class="seg" id="speed">${[0.5, 1, 2, 4].map((s) => `<button data-s="${s}" class="${s === 1 ? "active" : ""}">${s}×</button>`).join("")}</div>
        </div>

        <div class="card stack">
          <div><h2>Mark a theft</h2>
          <p class="small muted" style="margin:4px 0 0">Pause on the moment the item <b>disappears</b> into the pocket, bag or clothes (for grab-and-run: when they walk out the door), then tap the type. Keys <span class="kbd">1</span>–<span class="kbd">${typeKeys.length}</span> work too.</p></div>
          <div class="row" id="marks">${typeKeys.map((k, i) => `<button class="btn small" data-type="${k}"><span class="kbd">${i + 1}</span>${esc(TYPES[k])}</button>`).join("")}</div>
          <div class="list" id="evlist"></div>
        </div>

        <div class="card stack" id="zcard">
          <div class="card-head"><span class="ico-tile violet">${I.zone}</span><div class="grow"><h2>Camera angles &amp; shelf zones</h2>
            <p>Outline the shelves for each camera view. If the video switches to a different camera, add a new angle where the switch happens. Each angle gets its own zones.</p></div></div>
          <div class="list" id="scenes"></div>
          <div class="row" id="scene-actions">
            <button class="btn small" id="addscene">${ICON.cam}New angle starts here</button>
            <button class="btn small ghost" id="findcuts">${I.zap}Find angle changes automatically</button>
          </div>
          <div id="zpanel"></div>
        </div>

        ${result ? `<div class="card stack"><div><h2>What Oversyte did last scoring run</h2>
          <p class="small muted" style="margin:4px 0 0">Caught ${result.caught} of ${result.events}, ${result.false_alarms} false alarm${result.false_alarms === 1 ? "" : "s"}. Tap a row to jump there. Re-run <code>eval.py</code> after changing labels or zones.</p></div>
          <div class="list">${result.detections.map((d) => `<button class="item seek" data-t="${d.ts}">
            <span class="pill ${d.matched ? "false_alarm" : "new"}">${d.matched ? "Correct" : "False alarm"}</span>
            <span class="grow" style="text-align:left">${esc(d.reason)}</span><span class="mono">${fmtT(d.ts)}</span></button>`).join("")}
            ${result.missed.map((m) => `<button class="item seek" data-t="${m.t}"><span class="pill theft">Missed</span>
            <span class="grow" style="text-align:left">${esc(TYPES[m.type] || m.type)}</span><span class="mono">${fmtT(m.t)}</span></button>`).join("")}
            ${!result.detections.length && !result.missed.length ? `<p class="small muted" style="margin:0">No alerts and nothing missed.</p>` : ""}</div>
        </div>` : ""}

        <label class="field"><span>Notes (camera angle, lighting, anything unusual)</span>
          <textarea id="notes">${esc(lb.notes)}</textarea></label>
        <label class="field"><span>Labeled by</span>
          <input type="text" id="who" maxlength="60" placeholder="Your name" value="${esc(lb.labeled_by || myName())}"></label>
        <label class="card row" style="cursor:pointer"><input type="checkbox" id="done" ${lb.done ? "checked" : ""} style="width:22px;height:22px">
          <div class="grow"><b>Finished labeling this video</b><div class="small muted">I watched all of it and marked every theft. Only finished videos are scored. An unmarked moment counts as "no theft."</div></div></label>
        <div class="small muted" id="saved">&nbsp;</div>
      </div>`;

    const vid = $("#vid", view), tl = $("#tl", view), zov = $("#zov", view);
    let saveT;
    const save = () => {
      clearTimeout(saveT);
      $("#saved", view).textContent = "Saving…";
      if (!lb.labeled_by) lb.labeled_by = $("#who", view).value.trim();  // whoever edits first owns it
      saveT = setTimeout(async () => {
        try {
          lb = await api(`/api/lab/${enc}/labels`, { method: "PUT", body: lb });
          $("#saved", view).textContent = "All changes saved";
        } catch (e) { handleErr(e); }
      }, 400);
    };

    // ----- camera angles (scenes): each has its own zones, active from its start time -----
    const sceneAt = (t) => {
      let i = 0;
      lb.scenes.forEach((s, j) => { if (s.start <= t + 0.01) i = j; });
      return i;
    };
    let shownScene = -1;
    const drawOverlay = () => { shownScene = sceneAt(vid.currentTime); paintZones(zov, vid, lb.scenes[shownScene]); };
    const zoneText = (s) => {
      const n = s.shelf.length, m = s.ignore.length;
      return n || m ? `${n} shelf zone${n === 1 ? "" : "s"}${m ? `, ${m} ignore` : ""}` : `<span style="color:var(--warn)">no zones yet</span>`;
    };
    const renderScenes = () => {
      const cur = sceneAt(vid.currentTime);
      const withZones = lb.scenes.map((s, i) => [s, i]).filter(([s]) => s.shelf.length || s.ignore.length);
      $("#scenes", view).innerHTML = lb.scenes.map((s, i) => `
        <div class="item ${i === cur ? "cur" : ""}">
          <span class="ico-tile ${i === cur ? "" : "violet"}" style="width:34px;height:34px;font-weight:800;font-size:13px">${i + 1}</span>
          <div class="grow"><b>Angle ${i + 1}</b>${i === cur ? ` <span class="pill" style="margin-left:6px">On screen</span>` : ""}
            <div class="small muted">from <button class="linkbtn mono seek" data-t="${s.start + 1.5}" style="padding:0">${fmtT(s.start)}</button> · ${zoneText(s)}</div></div>
          ${!s.shelf.length && !s.ignore.length && withZones.length ? `<select data-copy="${i}" style="width:auto;padding:6px 8px;font-size:13px">
            <option value="">Same view as…</option>${withZones.map(([, j]) => `<option value="${j}">Angle ${j + 1}</option>`).join("")}</select>` : ""}
          <button class="btn small" data-draw="${i}">${I.zone}Draw zones</button>
          ${i > 0 ? `<button class="linkbtn danger small" data-rmscene="${i}" title="Remove this angle">${I.trash}</button>` : ""}
        </div>`).join("");
    };
    const addScene = (t, quiet) => {
      if (lb.scenes.some((s) => Math.abs(s.start - t) < 0.5)) { if (!quiet) toast("An angle already starts here"); return false; }
      lb.scenes.push({ start: +t.toFixed(2), shelf: [], ignore: [] });
      lb.scenes.sort((a, b) => a.start - b.start);
      return true;
    };
    const afterScenesChanged = () => { renderScenes(); renderTimeline(); drawOverlay(); save(); };

    const renderTimeline = () => {
      $$(".tick", tl).forEach((t) => t.remove());
      const D = vid.duration;
      if (!D) return;
      const add = (t, cls, title) => tl.insertAdjacentHTML("beforeend",
        `<div class="tick ${cls}" style="left:${(t / D) * 100}%" title="${esc(title)}"></div>`);
      lb.scenes.slice(1).forEach((s, i) => add(s.start, "cut", `Angle ${i + 2} starts ${fmtT(s.start)}`));
      (result?.detections || []).forEach((d) => add(d.ts, d.matched ? "hit" : "fa", `${d.matched ? "Correct alert" : "False alarm"} ${fmtT(d.ts)}`));
      lb.events.forEach((e) => add(e.t, "label", `${TYPES[e.type]} ${fmtT(e.t)}`));
    };
    const renderEvents = () => {
      $("#evlist", view).innerHTML = lb.events.length ? lb.events.map((e, i) => `
        <div class="item"><button class="linkbtn mono seek" data-t="${e.t}" style="color:var(--text)">${fmtT(e.t)}</button>
          <select data-i="${i}" class="grow" style="padding:6px 8px">${typeKeys.map((k) => `<option value="${k}" ${k === e.type ? "selected" : ""}>${esc(TYPES[k])}</option>`).join("")}</select>
          <button class="linkbtn danger small" data-del="${i}">Remove</button></div>`).join("")
        : `<p class="small muted" style="margin:0">No thefts marked. If this video has none, that's fine: tick "Finished labeling" below and it will test for false alarms.</p>`;
      renderTimeline();
    };
    const mark = (type) => {
      const t = +vid.currentTime.toFixed(2);
      if (lb.events.some((e) => Math.abs(e.t - t) < 0.5)) { toast("Already marked here"); return; }
      lb.events.push({ t, type });
      lb.events.sort((a, b) => a.t - b.t);
      renderEvents(); save();
      toast(`Marked ${TYPES[type]} at ${fmtT(t)}`);
    };
    const seek = (t) => { vid.currentTime = Math.max(0, Math.min(t, vid.duration || t)); };

    vid.addEventListener("loadedmetadata", () => { renderTimeline(); drawOverlay(); renderScenes(); });
    vid.addEventListener("timeupdate", () => {
      if (sceneAt(vid.currentTime) !== shownScene) { drawOverlay(); renderScenes(); }
      $("#now", view).textContent = fmtT(vid.currentTime);
      const f = vid.duration ? (vid.currentTime / vid.duration) * 100 : 0;
      $("#played", view).style.width = f + "%";
      $("#head", view).style.left = f + "%";
    });
    vid.addEventListener("play", () => ($("#play", view).textContent = "Pause"));
    vid.addEventListener("pause", () => ($("#play", view).textContent = "Play"));
    vid.addEventListener("click", () => (vid.paused ? vid.play() : vid.pause()));
    $("#play", view).onclick = () => (vid.paused ? vid.play() : vid.pause());
    $$("[data-j]", view).forEach((b) => (b.onclick = () => { vid.pause(); seek(vid.currentTime + +b.dataset.j); }));
    $("#speed", view).onclick = (e) => {
      const b = e.target.closest("button"); if (!b) return;
      vid.playbackRate = +b.dataset.s;
      $$("#speed button", view).forEach((x) => x.classList.toggle("active", x === b));
    };
    const scrub = (e) => {
      const r = tl.getBoundingClientRect();
      if (vid.duration) seek(((e.clientX - r.left) / r.width) * vid.duration);
    };
    tl.onpointerdown = (e) => { tl.setPointerCapture(e.pointerId); scrub(e); tl.onpointermove = scrub; };
    tl.onpointerup = () => (tl.onpointermove = null);
    $("#marks", view).onclick = (e) => { const b = e.target.closest("[data-type]"); if (b) mark(b.dataset.type); };
    view.addEventListener("click", (e) => {
      const s = e.target.closest(".seek");
      if (s) { vid.pause(); seek(+s.dataset.t - 1.5); window.scrollTo({ top: 0, behavior: "smooth" }); }
      const d = e.target.closest("[data-del]");
      if (d) { lb.events.splice(+d.dataset.del, 1); renderEvents(); save(); }
    });
    $("#evlist", view).onchange = (e) => {
      if (e.target.matches("select")) { lb.events[+e.target.dataset.i].type = e.target.value; renderTimeline(); save(); }
    };
    $("#notes", view).onchange = (e) => { lb.notes = e.target.value; save(); };
    $("#done", view).onchange = (e) => {
      lb.done = e.target.checked;
      if (!$("#who", view).value.trim()) { toast("Add your name in “Labeled by” too"); $("#who", view).focus(); }
      save();
    };
    $("#who", view).onchange = (e) => {
      lb.labeled_by = e.target.value.trim();
      try { localStorage.setItem("oversyte-labeler", lb.labeled_by); } catch (_) {}
      save();
    };
    const drawScene = async (i) => {
      vid.pause();
      // show a frame from this angle before drawing on it
      if (sceneAt(vid.currentTime) !== i) {
        const s = lb.scenes[i], next = lb.scenes[i + 1]?.start ?? vid.duration;
        await new Promise((r) => { vid.addEventListener("seeked", r, { once: true }); seek(Math.min(s.start + 0.5, (s.start + next) / 2)); });
      }
      zov.hidden = true;
      $("#scenes", view).hidden = $("#scene-actions", view).hidden = true;
      $("#vwrap", view).scrollIntoView({ block: "start", behavior: "smooth" });
      zoneDrawer({
        wrap: $("#zin", view), media: vid, panel: $("#zpanel", view), zones: lb.scenes[i],
        onSave: async (z) => {
          lb.scenes[i] = { ...lb.scenes[i], shelf: z.shelf, ignore: z.ignore };
          save(); toast(`Zones saved for angle ${i + 1}`);
        },
        onExit: () => {
          $("#zpanel", view).innerHTML = "";
          zov.hidden = false;
          $("#scenes", view).hidden = $("#scene-actions", view).hidden = false;
          drawOverlay(); renderScenes();
        },
      });
    };
    $("#scenes", view).addEventListener("click", (e) => {
      const d = e.target.closest("[data-draw]");
      if (d) drawScene(+d.dataset.draw);
      const r = e.target.closest("[data-rmscene]");
      if (r && confirm(`Remove angle ${+r.dataset.rmscene + 1} and its zones?`)) {
        lb.scenes.splice(+r.dataset.rmscene, 1);
        afterScenesChanged();
      }
    });
    $("#scenes", view).addEventListener("change", (e) => {
      const c = e.target.closest("[data-copy]");
      if (!c || c.value === "") return;
      const src = lb.scenes[+c.value], dst = +c.dataset.copy;
      lb.scenes[dst] = { ...lb.scenes[dst], shelf: structuredClone(src.shelf), ignore: structuredClone(src.ignore) };
      afterScenesChanged();
      toast(`Angle ${dst + 1} now uses the zones from angle ${+c.value + 1}`);
    });
    $("#addscene", view).onclick = () => {
      vid.pause();
      if (addScene(vid.currentTime)) {
        afterScenesChanged();
        toast(`New angle at ${fmtT(vid.currentTime)}. Now draw its zones.`);
      }
    };
    $("#findcuts", view).onclick = async (e) => {
      const btn = e.currentTarget, html = btn.innerHTML;
      btn.disabled = true;
      btn.textContent = "Looking for angle changes…";
      try {
        const { cuts } = await api(`/api/lab/${enc}/cuts`);
        const added = cuts.filter((t) => addScene(t, true)).length;
        if (added) afterScenesChanged();
        toast(!cuts.length ? "No angle changes found: this video uses one camera view"
          : added ? `Found ${added} angle change${added === 1 ? "" : "s"}. Draw zones for each new angle.`
          : "All angle changes were already marked");
      } catch (err) { handleErr(err); }
      finally { btn.disabled = false; btn.innerHTML = html; }
    };

    const onKey = (e) => {
      if (e.target.matches("input, textarea, select")) return;
      if (e.key === " ") { e.preventDefault(); vid.paused ? vid.play() : vid.pause(); }
      else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault(); vid.pause();
        seek(vid.currentTime + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 0.2 : 2));
      } else if (/^[1-9]$/.test(e.key) && typeKeys[+e.key - 1]) mark(typeKeys[+e.key - 1]);
      else if (e.key === "+" || e.key === "=") zoom.by(1.5);
      else if (e.key === "-" || e.key === "_") zoom.by(1 / 1.5);
      else if (e.key === "0") zoom.reset();
    };
    const zoom = makeZoomable($("#vwrap", view), $("#zin", view), (z) => {
      $("#zlevel", view).textContent = `${Math.round(z * 100)}%`;
      $("#zreset", view).hidden = z <= 1.001;
    });
    $("#zctl", view).onclick = (e) => {
      const b = e.target.closest("button"); if (!b) return;
      e.stopPropagation();
      ({ in: () => zoom.by(1.5), out: () => zoom.by(1 / 1.5), reset: () => zoom.reset() })[b.dataset.z]();
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", drawOverlay);
    state.cleanup.push(() => { document.removeEventListener("keydown", onKey); window.removeEventListener("resize", drawOverlay); vid.pause(); });
    renderEvents();
    renderScenes();

    // Recorder exports are often H.265 etc.; the server makes a browser copy the first time.
    let alive = true;
    state.cleanup.push(() => (alive = false));
    const vmsg = $("#vmsg", view);
    for (let first = true; alive; first = false) {
      const p = await api(`/api/lab/${enc}/playable`);
      if (p.ready) { vid.src = `/api/lab/${enc}/video`; vmsg.remove(); break; }
      if (p.error) { vmsg.innerHTML = `<span>${esc(p.error)}</span>`; break; }
      if (first) $("span", vmsg).textContent = "Converting this video so your browser can play it (first time only)…";
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  // ---------- settings / push ----------
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isStandalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  function b64ToU8(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function currentSub() {
    if (!pushSupported) return null;
    const reg = await navigator.serviceWorker.getRegistration();
    return reg ? reg.pushManager.getSubscription() : null;
  }

  async function enablePush(label) {
    const reg = await navigator.serviceWorker.ready;
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("Notifications are blocked. Allow them in your browser or phone settings.");
    const { key } = await api("/api/push/key");
    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      const k = sub.options?.applicationServerKey;
      const same = k && btoa(String.fromCharCode(...new Uint8Array(k))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") === key;
      if (!same) { await sub.unsubscribe(); sub = null; }
    }
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(key) });
    await api("/api/push/subscribe", { method: "POST", body: { subscription: sub.toJSON(), label } });
    localStorage.setItem("oversyte-device", label);
  }

  function guessDevice() {
    const ua = navigator.userAgent;
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua) || isIOS) return "iPad";
    if (/Android/.test(ua)) return "Android phone";
    if (/Windows/.test(ua)) return "Windows PC";
    if (/Mac/.test(ua)) return "Mac";
    return "This device";
  }

  async function settingsView() {
    const view = shell("settings", pageHead("Configure", "Settings", "Phone alerts, devices and data.") + `<div id="s"></div>`);
    const s = $("#s", view);
    const sub = await currentSub().catch(() => null);
    const devices = await api("/api/push/devices");
    const mine = sub && devices.find((d) => d.endpoint === sub.endpoint);
    let savedName = "";
    try { savedName = localStorage.getItem("oversyte-device") || ""; } catch (_) {}

    let pushBlock;
    if (DEMO) {
      pushBlock = `<div class="banner info" style="margin:0">${I.phone}<div>In the installed product, owner and staff phones get every alert instantly, with the video clip. This online demo shows recorded footage, so phone alerts are switched off here.</div></div>`;
    } else if (!window.isSecureContext) {
      pushBlock = `<div class="banner warn" style="margin:0">Phone alerts need a secure (https://) address. Open the dashboard through your HTTPS link (see README → "Phone alerts"), or use http://localhost on this computer.</div>`;
    } else if (isIOS && !isStandalone) {
      pushBlock = `<div class="banner info" style="margin:0"><b>On iPhone:</b> tap the Share button, choose <b>Add to Home Screen</b>, then open Oversyte from your home screen and come back here to turn on alerts.</div>`;
    } else if (!pushSupported) {
      pushBlock = `<div class="banner warn" style="margin:0">This browser doesn't support push notifications. Try Chrome on Android, or Safari on iPhone (iOS 16.4+, added to the home screen).</div>`;
    } else if (mine) {
      pushBlock = `<div class="list"><div class="item"><span class="status-dot on"></span><div class="grow"><b>Alerts are on for this device</b><div class="small muted">${esc(mine.label)}</div></div>
        <button class="btn small ghost" id="off">Turn off</button></div></div>`;
    } else {
      pushBlock = `<label class="field"><span>Name this device (so you can tell who gets alerts)</span>
        <input type="text" id="dname" maxlength="60" value="${esc(savedName || guessDevice())}"></label>
        <button class="btn primary block" id="on">${ICON.bell}Turn on theft alerts</button>`;
    }

    s.innerHTML = `
      <div class="cols">
        <div class="card stack">
          <div class="card-head"><span class="ico-tile red">${I.phone}</span><div class="grow"><h2>Phone alerts</h2>
            <p>Each owner or staff phone signs in once and turns alerts on.</p></div></div>
          ${pushBlock}
          <button class="btn block" id="test">${I.send}Send a test alert</button>
        </div>
        <div class="card stack">
          <div class="card-head"><span class="ico-tile">${ICON.bell}</span><div class="grow"><h2>Devices getting alerts</h2>
            <p>${devices.length} device${devices.length === 1 ? "" : "s"} will be notified.</p></div></div>
          ${devices.length ? `<div class="list">${devices.map((d, i) => `<div class="item"><span class="ico-tile" style="width:34px;height:34px">${I.phone}</span><div class="grow"><b>${esc(d.label)}</b>
            <div class="small muted">added ${ago(d.created)}${sub && d.endpoint === sub.endpoint ? " · this device" : ""}</div></div>
            <button class="linkbtn danger small" data-i="${i}">Remove</button></div>`).join("")}</div>`
            : `<div class="banner info" style="margin:0">${I.alert}<div>No devices yet. Nobody gets a phone alert until at least one is added.</div></div>`}
        </div>
        <div class="card stack">
          <div class="card-head"><span class="ico-tile green">${I.download}</span><div class="grow"><h2>Reviewed clips</h2>
            <p>Every clip marked theft or false alarm, with labels. Useful for police reports.</p></div></div>
          ${DEMO ? `<p class="small muted">Available in the installed product.</p>` : `<a class="btn" href="/api/export.zip">${I.download}Download labeled clips (.zip)</a>`}
        </div>
        <div class="card stack">
          <div class="card-head"><span class="ico-tile violet">${I.lock}</span><div class="grow"><h2>Account</h2>
            <p>Signed in to ${esc(state.me.store_name)}.</p></div></div>
          <button class="btn" id="logout">${I.logout}Sign out</button>
        </div>
      </div>`;

    $("#test", s).onclick = (e) => sendTest(null, e.currentTarget);
    $("#on", s)?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try { await enablePush($("#dname", s).value.trim() || guessDevice()); toast("Alerts are on"); settingsView(); }
      catch (err) { handleErr(err); btn.disabled = false; }
    });
    $("#off", s)?.addEventListener("click", async () => {
      await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } });
      await sub.unsubscribe().catch(() => {});
      toast("Alerts turned off for this device"); settingsView();
    });
    $$(".list .linkbtn", s).forEach((b) => (b.onclick = async () => {
      const d = devices[+b.dataset.i];
      if (!confirm(`Stop sending alerts to "${d.label}"?`)) return;
      await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: d.endpoint } });
      if (sub && d.endpoint === sub.endpoint) await sub.unsubscribe().catch(() => {});
      settingsView();
    }));
    $("#logout", s).onclick = async () => { await api("/api/logout", { method: "POST" }); state.me.authed = false; renderLogin(); };
  }

  // ---------- background polling: badge + in-app alert ----------
  async function refreshBadge() {
    try {
      const [st, cams] = await Promise.all([api("/api/stats"), api("/api/cameras")]);
      state.unreviewed = st.unreviewed;
      state.cams = { total: cams.length, online: cams.filter((c) => c.connected).length };
      $$("[data-live-chip]").forEach((el) => (el.outerHTML = liveChip()));
      const side = $("#side-status");
      if (side) side.textContent = sideStatus();
      const tab = $('.nav a[href="#/"]');
      if (tab) {
        $(".dot", tab)?.remove();
        if (st.unreviewed) tab.insertAdjacentHTML("beforeend", `<span class="dot">${st.unreviewed > 99 ? "99+" : st.unreviewed}</span>`);
      }
      const [latest] = await api("/api/events?limit=1");
      if (latest) {
        if (state.newestTs && latest.ts > state.newestTs && !location.hash.startsWith("#/event/")) {
          toast(`Possible theft: ${latest.camera_name}. Tap to view.`, { alert: true, ms: 10000,
            onClick: () => (location.hash = `#/event/${latest.id}`) });
        }
        state.newestTs = Math.max(state.newestTs, latest.ts);
      }
    } catch (_) {}
  }
  function startPolling() {
    if (startPolling._t) return;
    refreshBadge();
    startPolling._t = setInterval(refreshBadge, 6000);
  }

  // ---------- router ----------
  function handleErr(e) {
    if (e instanceof AuthError) return;
    console.error(e);
    toast(e.message || "Something went wrong");
  }

  async function route() {
    if (!state.me.authed) return renderLogin();
    cleanupView();
    const h = location.hash.replace(/^#/, "") || "/";
    const [, page, arg] = h.split("/");
    try {
      if (!page) await alertsView();
      else if (page === "event") await eventView(decodeURIComponent(arg));
      else if ((page === "cameras" || page === "camera") && !hasCams()) location.hash = "#/";
      else if (page === "cameras") await camerasView();
      else if (page === "camera") await cameraView(decodeURIComponent(arg));
      else if (page === "settings") await settingsView();
      else if (page === "lab" && !team()) location.hash = "#/";
      else if (page === "lab") await (arg ? labelView(decodeURIComponent(arg)) : labView());
      else location.hash = "#/";
    } catch (e) { handleErr(e); }
  }

  window.addEventListener("hashchange", route);

  if ("serviceWorker" in navigator && !DEMO) {
    navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("SW failed", e));
    navigator.serviceWorker.addEventListener("message", (e) => {
      if (e.data?.type === "open" && e.data.url) location.href = e.data.url;
    });
  }

  (async () => {
    try { state.me = await (await fetch("/api/me", { credentials: "same-origin" })).json(); } catch (_) {}
    if (state.me.authed) startPolling();
    route();
  })();
})();
