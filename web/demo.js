/* Oversyte static demo: answers the dashboard's /api/* requests from demo-data.json, so the
   site runs on any static host. Only loaded when window.OVERSYTE_DEMO is set (static build). */
(() => {
  "use strict";
  const CFG = window.OVERSYTE_DEMO;
  if (!CFG) return;
  const realFetch = window.fetch.bind(window);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("ovd-" + k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem("ovd-" + k, JSON.stringify(v)); } catch (_) {} },
  };
  const authed = () => { try { return sessionStorage.getItem("ovd-auth") === "1"; } catch (_) { return false; } };
  const setAuthed = (on) => { try { on ? sessionStorage.setItem("ovd-auth", "1") : sessionStorage.removeItem("ovd-auth"); } catch (_) {} };
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
  const fail = (status, detail) => json({ detail }, status);

  let data;
  const ready = realFetch(CFG.data).then((r) => r.json()).then((d) => { data = d; });

  // alerts with a timestamp relative to "now", plus the viewer's own reviews, deletions and test alerts
  function events() {
    const now = Date.now() / 1000;
    const reviews = store.get("reviews", {}), deleted = new Set(store.get("deleted", []));
    return [...data.events.map((e) => ({ ...e, ts: now - e.ago })), ...store.get("tests", [])]
      .filter((e) => !deleted.has(e.id))
      .map((e) => ({ ...e, ...(reviews[e.id] || {}) }))
      .sort((a, b) => b.ts - a.ts);
  }
  const midnight = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; };

  function stats() {
    const real = events().filter((e) => !e.is_test), day = midnight(), week = day - 6 * 86400;
    return {
      today: real.filter((e) => e.ts >= day).length,
      week: real.filter((e) => e.ts >= week).length,
      unreviewed: real.filter((e) => e.status === "new").length,
      theft_week: real.filter((e) => e.status === "theft" && e.ts >= week).length,
      false_alarm_week: real.filter((e) => e.status === "false_alarm" && e.ts >= week).length,
    };
  }

  function daily() {
    const real = events().filter((e) => !e.is_test), out = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - i);
      const a = d.getTime() / 1000, b = a + 86400, day = real.filter((e) => e.ts >= a && e.ts < b);
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      out.push({ date: iso, alerts: day.length, theft: day.filter((e) => e.status === "theft").length,
        false_alarm: day.filter((e) => e.status === "false_alarm").length, open: day.filter((e) => e.status === "new").length });
    }
    return out;
  }

  const cameras = () => data.cameras.map((c) => ({ ...c, connected: true, ai_ready: true, error: null,
    frame_age: 0.2, fps: 12, size: [720, 405], people: c.people }));

  async function sha256(text) {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function route(url, method, body) {
    const p = url.pathname, q = url.searchParams;
    if (p === "/api/me") return json({ authed: authed(), store_name: data.store_name, default_password: false,
      cameras: data.cameras.length, team_mode: false, demo: true });
    if (p === "/api/login" && method === "POST") {
      if ((await sha256(body?.password || "")) !== data.password_sha256) return fail(401, "Wrong password");
      setAuthed(true); return json({ ok: true });
    }
    if (p === "/api/logout") { setAuthed(false); return json({ ok: true }); }
    if (!authed()) return fail(401, "Sign in required");

    if (p === "/api/stats") return json(stats());
    if (p === "/api/overview") {
      const c = cameras();
      return json({ stats: stats(), daily: daily(), devices: 0, zones: 1,
        cameras: { total: c.length, online: c.length, ai_ready: c.length },
        has_test_alert: store.get("tests", []).length > 0,
        accuracy: { catch_rate: null, false_alarms_per_hour: null, videos: 0, labeled: 0, run_at: null } });
    }
    if (p === "/api/events" && method === "GET") {
      let list = events();
      if (q.get("status")) list = list.filter((e) => e.status === q.get("status"));
      if (q.get("before")) list = list.filter((e) => e.ts < +q.get("before"));
      return json(list.slice(0, +(q.get("limit") || 30)));
    }
    const m = p.match(/^\/api\/events\/([\w-]+)$/);
    if (m) {
      const ev = events().find((e) => e.id === m[1]);
      if (!ev) return fail(404, "Not found");
      if (method === "GET") return json(ev);
      if (method === "PATCH") {
        const reviews = store.get("reviews", {}), r = reviews[ev.id] || {};
        if (body.status != null) r.status = body.status;
        if (body.notes != null) r.notes = body.notes;
        reviews[ev.id] = r; store.set("reviews", reviews);
        return json({ ...ev, ...r });
      }
      if (method === "DELETE") { store.set("deleted", [...store.get("deleted", []), ev.id]); return json({ ok: true }); }
    }
    if (p === "/api/test-alert" && method === "POST") {
      const src = data.events[0] || {};
      const ev = { id: "test" + Date.now(), camera_id: "-", camera_name: data.cameras[0]?.name || "Aisle 1",
        ts: Date.now() / 1000, score: 1, region: src.region || null, status: "new", notes: "", is_test: 1,
        reason: "Test alert: this is what a real alert looks like", clip_url: src.clip_url || null, thumb_url: src.thumb_url || null };
      store.set("tests", [...store.get("tests", []), ev]);
      return json(ev);
    }
    if (p === "/api/cameras") return json(cameras());
    if (/^\/api\/cameras\/[\w-]+\/zones$/.test(p)) return json({ shelf: [], ignore: [] });
    if (p === "/api/push/devices") return json([]);
    if (p.startsWith("/api/push/")) return fail(400, "Phone alerts are part of the installed product, not this online demo.");
    return fail(404, "Not available in the demo");
  }

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return realFetch(input, init);
    await ready;
    const method = (init.method || "GET").toUpperCase();
    let body = null;
    try { body = init.body ? JSON.parse(init.body) : null; } catch (_) {}
    return route(url, method, body);
  };
})();
