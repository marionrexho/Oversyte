(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const next = (() => {
    const raw = new URLSearchParams(location.search).get("next") || "/";
    // only paths on this site: "//other.site" and "/\other.site" would leave it
    return /^\/(?![\/\\])/.test(raw) ? raw : "/";
  })();

  const icons = {
    alert: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4"></path><path d="M12 17h.01"></path><path d="M10.3 4.9 2.9 18a2 2 0 0 0 1.7 3h15a2 2 0 0 0 1.7-3l-7.4-13.1a2 2 0 0 0-3.6 0Z"></path></svg>`,
    user: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21a8 8 0 1 0-16 0"></path><circle cx="12" cy="7" r="4"></circle></svg>`,
    mail: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="m3 7 9 6 9-6"></path></svg>`,
    lock: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="9" rx="2"></rect><path d="M8 11V8a4 4 0 1 1 8 0v3"></path></svg>`,
    eye: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"></path><circle cx="12" cy="12" r="3"></circle></svg>`,
    eyeOff: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3 3 18 18"></path><path d="M10.6 10.6a3 3 0 0 0 4.2 4.2"></path><path d="M9.9 5.4A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a17.7 17.7 0 0 1-3.7 4.8"></path><path d="M6.1 6.1C3.7 8.1 2 12 2 12s3.5 7 10 7c1 0 1.9-.1 2.8-.3"></path></svg>`,
    check: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"></path></svg>`,
    chevron: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6"></path></svg>`,
    shield: `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 20 6v6c0 5-3.2 8.6-8 11-4.8-2.4-8-6-8-11V6l8-3Z"></path><path d="M9 12l2 2 4-4"></path></svg>`,
  };

  const fillIcons = () => {
    document.querySelectorAll("[data-icon]").forEach((el) => {
      el.innerHTML = icons[el.dataset.icon] || "";
    });
  };

  const setError = (msg = "") => {
    const el = $("#auth-err");
    if (el) el.textContent = msg;
  };

  const togglePeek = (btn, input) => {
    const hidden = input.type === "password";
    input.type = hidden ? "text" : "password";
    btn.dataset.state = hidden ? "on" : "off";
    btn.setAttribute("aria-label", hidden ? "Hide password" : "Show password");
    btn.setAttribute("title", hidden ? "Hide password" : "Show password");
    btn.innerHTML = icons[hidden ? "eyeOff" : "eye"];
    input.focus();
  };

  const gotoNext = () => location.replace(next);

  const api = async (path, body) => {
    const res = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Something went wrong");
    return data;
  };

  const syncState = async () => {
    try {
      const me = await (await fetch("/api/me", { credentials: "same-origin" })).json();
      if (me.authed) {
        gotoNext();
        return;
      }
      if (me.store_name && $("#store-name")) $("#store-name").textContent = me.store_name;
      if (me.store_name && $("#store-name-inline")) $("#store-name-inline").textContent = me.store_name;
      // Only the store's first account can sign up; everyone else is added from Settings -> Team.
      const sw = $(".auth-switch");
      if (page === "login" && sw) {
        if (me.signup_open) sw.innerHTML = `Setting up this store? <a href="/auth/signup">Create the owner account</a>`;
        else sw.textContent = "Need an account? Ask your store owner to add you.";
      }
      if (page === "signup" && me.signup_open === false && form) {
        form.innerHTML = `<img class="logo" src="/brand/oversyte-logo-sm.png" alt="Oversyte">
          <div class="auth-heading"><h2>Ask your store owner</h2>
          <p>This store already has an owner account. They can add you in Settings → Team.</p></div>
          <a class="btn primary block signin" href="/auth/login">Go to sign in</a>`;
      }
    } catch (_) {}
  };

  fillIcons();
  syncState();

  const year = $("#year");
  if (year) year.textContent = new Date().getFullYear();

  const form = $("#auth-form");
  const page = document.body.dataset.page || "login";
  const peek = $(".peek");
  const password = $("input[name=password]");
  if (peek && password) {
    peek.addEventListener("click", () => togglePeek(peek, password));
  }

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("");
    const submit = $("button[type=submit]", form);
    const fields = form.elements;
    submit.disabled = true;
    try {
      if (page === "signup") {
        const name = fields.name.value.trim();
        const email = fields.email.value.trim();
        const pass = fields.password.value;
        const confirm = fields.confirm_password.value;
        if (pass !== confirm) throw new Error("Passwords do not match");
        await api("/api/signup", { name, email, password: pass });
      } else {
        await api("/api/login", { email: fields.email.value.trim(), password: fields.password.value });
      }
      gotoNext();
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      submit.disabled = false;
    }
  });
})();
