/* Kitchen Planner card: iOS-style views for the Kitchen dashboard.
 * Config: { type: custom:kitchen-planner-card, view: week | shop | pantry | spend }
 * Reads the kitchen_planner sensors/todo; writes only through HA services. */
const KP = {
  dinners: "sensor.kitchen_planner_dinners_planned",
  total: "sensor.kitchen_planner_estimated_total",
  staples: "sensor.kitchen_planner_staples_due",
  leftovers: "sensor.kitchen_planner_leftovers",
  house: "sensor.kitchen_planner_in_the_house",
  spend: "sensor.kitchen_planner_weekly_spend",
  todo: "todo.kitchen_planner_coles_order",
  ready: "input_boolean.kitchen_order_ready",
  refresh: "button.kitchen_planner_refresh",
};
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const AISLE_ICON = {
  "fruit & vegetables": ["mdi:carrot", "#34C759"], "meat & seafood": ["mdi:food-steak", "#FF3B30"],
  "deli": ["mdi:food-drumstick", "#FF9500"], "dairy, eggs & fridge": ["mdi:cheese", "#FFCC00"],
  "bakery": ["mdi:baguette", "#A2845E"], "pantry": ["mdi:archive", "#5856D6"],
  "frozen": ["mdi:snowflake", "#32ADE6"], "drinks": ["mdi:cup", "#007AFF"],
  "household": ["mdi:spray-bottle", "#8E8E93"], "health & beauty": ["mdi:lotion", "#FF2D55"],
};
const PANTRY = [
  ["herb_parsley", "Parsley", "mdi:leaf", "#34C759"], ["herb_coriander", "Coriander", "mdi:leaf", "#30D158"],
  ["herb_basil", "Basil", "mdi:leaf", "#248A3D"], ["cream_thickened", "Thickened cream", "mdi:cup", "#FF9500"],
  ["yoghurt_greek", "Greek yoghurt", "mdi:cup-outline", "#64D2FF"], ["cheese_feta", "Feta", "mdi:cheese", "#FFCC00"],
  ["tomato_cherry", "Cherry tomatoes", "mdi:fruit-cherries", "#FF3B30"], ["lemon", "Lemons", "mdi:fruit-citrus", "#FFD60A"],
  ["mushroom", "Mushrooms", "mdi:mushroom", "#A2845E"], ["zucchini", "Zucchini", "mdi:carrot", "#30B0C7"],
];
const SWITCH = { herb_parsley: "parsley", herb_coriander: "coriander", herb_basil: "basil",
  cream_thickened: "thickened_cream", yoghurt_greek: "greek_yoghurt", cheese_feta: "feta",
  tomato_cherry: "cherry_tomatoes", lemon: "lemons", mushroom: "mushrooms", zucchini: "zucchini" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v === null || v === undefined || isNaN(Number(v)) ? "–" : "$" + Number(v).toFixed(2));
/* "thickened cream: 1 x Coles Thickened Cream 300mL ($2.60)" -> title/sub/value;
   "Coles Full Cream Milk 3L x1" -> title/value; descriptions shortened. */
const parseLine = (it) => {
  const sum = String(it.summary || "");
  const desc = String(it.description || "");
  if (it.uid.startsWith("kp:staple:")) {
    const m = sum.match(/^(.*) x(\d+)$/);
    const d = desc.match(/every (\d+)d, last bought (\d{4}-\d{2}-\d{2}), ~\$([\d.]+)/);
    return { title: m ? m[1] : sum, value: m ? "×" + m[2] : "",
      sub: d ? `Every ${d[1]} days · last ${fmtDate(d[2], { day: "numeric", month: "short" })} · ~$${Number(d[3]).toFixed(2)}` : desc };
  }
  if (it.uid.startsWith("kp:")) {
    const m = sum.match(/^([^:]+):\s*(.*?)(?:\s*\((\$[\d.]+)\))?$/);
    const [forPart, ...rest] = desc.split(" | ");
    const note = rest.find((r) => r.startsWith("No pack")) ? "Choose the size in the trolley" : "";
    return { title: m ? cap(m[1]) : sum, value: m && m[3] ? m[3] : "",
      sub: [m ? m[2].replace(/ x /g, " × ") : "", note || forPart.replace(/^For: /, "for ")].filter(Boolean).join(" · ") };
  }
  return { title: sum, sub: desc, value: "" };
};
const cap = (s) => String(s || "").replace(/^\w/, (c) => c.toUpperCase());
const addDays = (iso, n) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d + n); };
const fmtDate = (iso, opts) => { try { return new Date(iso + "T00:00:00").toLocaleDateString("en-AU", opts); } catch (e) { return iso; } };

const CSS = `
:host { --bg:#F2F2F7; --card:#FFFFFF; --label:#000; --label2:rgba(60,60,67,.6); --label3:rgba(60,60,67,.3);
  --sep:rgba(60,60,67,.18); --fill:rgba(120,120,128,.12); --blue:#007AFF; --green:#34C759; --orange:#FF9500; --red:#FF3B30;
  display:block; }
:host([dark]) { --bg:#000; --card:#1C1C1E; --label:#FFF; --label2:rgba(235,235,245,.6); --label3:rgba(235,235,245,.3);
  --sep:rgba(84,84,88,.6); --fill:rgba(118,118,128,.24); --blue:#0A84FF; --green:#30D158; --orange:#FF9F0A; --red:#FF453A; }
* { box-sizing:border-box; }
.page { background:var(--bg); color:var(--label); min-height:100vh; padding:8px 16px 48px;
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Roboto,Segoe UI,sans-serif;
  -webkit-font-smoothing:antialiased; }
.wrap { max-width:1100px; margin:0 auto; }
.top { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; margin:12px 4px 18px; }
.eyebrow { font-size:13px; font-weight:600; color:var(--label2); text-transform:uppercase; letter-spacing:.04em; }
h1 { font-size:34px; line-height:41px; font-weight:700; letter-spacing:.01em; margin:2px 0 0; }
.pill { border:0; background:var(--fill); color:var(--blue); font-family:inherit; font-size:15px; line-height:20px; font-weight:600;
  padding:8px 14px; border-radius:999px; cursor:pointer; display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }
.pill ha-icon { --mdc-icon-size:18px; }
.pill.filled { background:var(--blue); color:#fff; }
.widgets { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:24px; }
.widget { background:var(--card); border-radius:16px; padding:14px; display:flex; flex-direction:column; gap:10px; min-height:96px; }
.w-icon { width:30px; height:30px; border-radius:50%; display:flex; align-items:center; justify-content:center; color:#fff; }
.w-icon ha-icon { --mdc-icon-size:18px; }
.w-val { font-size:28px; font-weight:700; line-height:30px; letter-spacing:-.01em; }
.w-lab { font-size:13px; color:var(--label2); font-weight:500; }
.cols { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 24px; align-items:start; }
.col { min-width:0; }
.gh { font-size:13px; color:var(--label2); text-transform:uppercase; letter-spacing:.04em; margin:0 16px 7px; display:flex; justify-content:space-between; }
.group { background:var(--card); border-radius:12px; overflow:hidden; margin-bottom:8px; }
.gf { font-size:13px; color:var(--label2); margin:0 16px 26px; line-height:18px; }
.row { display:flex; align-items:center; gap:12px; padding:0 16px; min-height:44px; position:relative; }
.row + .row::before { content:""; position:absolute; top:0; right:0; left:var(--inset,16px); border-top:.5px solid var(--sep); }
.row.icon { --inset:58px; } .row.chk { --inset:52px; }
.row.tap { cursor:pointer; } .row.tap:active { background:var(--fill); }
.ri { width:30px; height:30px; border-radius:7px; display:flex; align-items:center; justify-content:center; color:#fff; flex:none; }
.ri ha-icon { --mdc-icon-size:18px; }
.rt { flex:1; min-width:0; padding:11px 0; }
.rt .t { font-size:17px; line-height:22px; overflow:hidden; text-overflow:ellipsis; }
.rt .s { font-size:13px; line-height:18px; color:var(--label2); margin-top:1px; }
.rv { color:var(--label2); font-size:17px; white-space:nowrap; }
.muted { color:var(--label3); }
.chev { color:var(--label3); --mdc-icon-size:20px; }
.check { width:24px; height:24px; border-radius:50%; border:1.5px solid var(--label3); flex:none; display:flex;
  align-items:center; justify-content:center; cursor:pointer; background:none; padding:0; color:#fff; }
.check.on { background:var(--blue); border-color:var(--blue); }
.check ha-icon { --mdc-icon-size:16px; }
.done .t, .done .rv { color:var(--label3); } .done .t { text-decoration:line-through; }
.x { border:0; background:none; color:var(--label3); cursor:pointer; padding:6px; margin-right:-6px; }
.x ha-icon { --mdc-icon-size:18px; }
.sw { width:51px; height:31px; border-radius:16px; background:var(--fill); position:relative; flex:none; cursor:pointer;
  border:0; padding:0; transition:background .2s; }
.sw::after { content:""; position:absolute; top:2px; left:2px; width:27px; height:27px; border-radius:50%; background:#fff;
  box-shadow:0 3px 8px rgba(0,0,0,.15),0 1px 1px rgba(0,0,0,.16); transition:transform .2s; }
.sw.on { background:var(--green); } .sw.on::after { transform:translateX(20px); }
.add { display:flex; gap:10px; align-items:center; padding:0 16px; min-height:44px; }
.add input { flex:1; border:0; background:none; font-family:inherit; font-size:17px; line-height:22px; color:var(--label); outline:none; padding:11px 0; }
.add input::placeholder { color:var(--label3); }
.add ha-icon { color:var(--blue); }
.banner { display:flex; gap:12px; align-items:flex-start; padding:12px 16px; }
.banner ha-icon { color:var(--orange); flex:none; }
.banner .t { font-size:15px; line-height:20px; }
.big { padding:16px; }
.big .n { font-size:40px; font-weight:700; letter-spacing:-.02em; }
.big .l { font-size:15px; color:var(--label2); }
.chart { padding:16px 12px 8px; }
.chart svg { width:100%; height:220px; display:block; }
.badge { font-size:13px; font-weight:600; color:#fff; background:var(--red); border-radius:999px; padding:1px 8px; }
.cta { display:block; width:100%; border:0; border-radius:12px; padding:15px; font-family:inherit; font-size:17px; line-height:22px; font-weight:600;
  background:var(--blue); color:#fff; cursor:pointer; margin-bottom:8px; }
.cta.off { background:var(--card); color:var(--blue); }
@media (max-width: 760px) { .cols { grid-template-columns:1fr; } .widgets { grid-template-columns:repeat(2,minmax(0,1fr)); } h1 { font-size:30px; } }
`;

class KitchenPlannerCard extends HTMLElement {
  setConfig(config) {
    this._view = (config && config.view) || "week";
    this._todo = null;
    this._todoStamp = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }
  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full" }; }

  set hass(hass) {
    this._hass = hass;
    const dark = !!(hass.themes && hass.themes.darkMode);
    if (dark) this.setAttribute("dark", ""); else this.removeAttribute("dark");
    const tstate = hass.states[KP.todo];
    const stamp = tstate ? tstate.last_updated + tstate.state : null;
    if (this._view === "shop" && stamp !== this._todoStamp) { this._todoStamp = stamp; this._loadTodo(); }
    const key = this._renderKey();
    if (key !== this._lastKey) { this._lastKey = key; this._render(); }
  }

  _renderKey() {
    const ids = Object.values(KP).concat(Object.values(SWITCH).map((s) => "switch.kitchen_planner_have_" + s));
    return ids.map((id) => { const s = this._hass.states[id]; return s ? s.last_updated : "-"; }).join("|")
      + JSON.stringify(this._todo || null) + (this._hass.themes && this._hass.themes.darkMode);
  }
  _s(id) { return this._hass.states[id]; }
  _a(id, attr, dflt) { const s = this._s(id); return s && s.attributes[attr] !== undefined ? s.attributes[attr] : dflt; }

  async _loadTodo() {
    try {
      const r = await this._hass.callWS({ type: "todo/item/list", entity_id: KP.todo });
      this._todo = r.items || [];
    } catch (e) { this._todo = []; }
    this._lastKey = null; this.hass = this._hass;
  }
  _nav(path) { history.pushState(null, "", path); window.dispatchEvent(new CustomEvent("location-changed")); }
  _call(domain, service, data, target) { return this._hass.callService(domain, service, data || {}, target); }

  _widget(icon, color, value, label, nav) {
    return `<div class="widget${nav ? " tapw" : ""}" ${nav ? `data-nav="${nav}" style="cursor:pointer"` : ""}>
      <div class="w-icon" style="background:${color}"><ha-icon icon="${icon}"></ha-icon></div>
      <div><div class="w-val">${esc(value)}</div><div class="w-lab">${esc(label)}</div></div></div>`;
  }
  _widgets() {
    const n = (id) => { const s = this._s(id); return s ? s.state : "–"; };
    return `<div class="widgets">
      ${this._widget("mdi:silverware-fork-knife", "#34C759", n(KP.dinners) + "/7", "Dinners planned", "/kitchen-planner/week")}
      ${this._widget("mdi:cart", "#007AFF", money(n(KP.total)), this._a(KP.total, "is_floor", false) ? "Estimate (at least)" : "Estimate", "/kitchen-planner/shopping")}
      ${this._widget("mdi:basket", "#FF9500", n(KP.staples), "Staples due", "/kitchen-planner/shopping")}
      ${this._widget("mdi:fridge-alert", "#FF3B30", n(KP.leftovers), "Leftovers", "/kitchen-planner/week")}
    </div>`;
  }
  _top(eyebrow, title, actions) {
    return `<div class="top"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1></div><div>${actions || ""}</div></div>`;
  }
  _unavailable() {
    return `<div class="group"><div class="banner"><ha-icon icon="mdi:alert-circle-outline"></ha-icon>
      <div class="t">Kitchen Planner isn't ready. Check the integration under Settings → Devices &amp; services.</div></div></div>`;
  }

  /* ---------- views ---------- */
  _week() {
    const days = this._a(KP.dinners, "days", []);
    const ws = this._a(KP.dinners, "week_start", null);
    const unmapped = this._a(KP.dinners, "unmapped", []);
    const left = this._a(KP.leftovers, "items", []);
    const rows = DAYS.map((d, i) => {
      const meals = days.filter((x) => x.day === d).map((x) => x.name);
      const date = ws ? addDays(ws, i).toLocaleDateString("en-AU", { day: "numeric", month: "short" }) : "";
      return `<div class="row tap" data-nav="/kitchen-planner/recipes"><div class="rt">
        <div class="s">${esc(d)} ${esc(date)}</div>
        <div class="t ${meals.length ? "" : "muted"}">${meals.length ? esc(meals.join(", ")) : "Nothing planned"}</div></div>
        <ha-icon class="chev" icon="mdi:chevron-right"></ha-icon></div>`;
    }).join("");
    const warn = unmapped.length ? `<div class="group"><div class="banner"><ha-icon icon="mdi:alert"></ha-icon><div class="t">
      <b>Not in the planner yet:</b> ${esc(unmapped.join(", "))}. Nothing gets bought for these until they're added.</div></div></div>` : "";
    const lrows = left.length ? left.map((l) => `<div class="row icon"><div class="ri" style="background:#FF3B30"><ha-icon icon="mdi:food-variant"></ha-icon></div>
        <div class="rt"><div class="t">${esc(cap(l.label))}</div><div class="s">${esc(l.waste_pct)}% of the pack spare</div></div><div class="rv">${esc(l.display)}</div></div>`).join("")
      : `<div class="row"><div class="rt"><div class="t muted">Nothing left over yet</div></div></div>`;
    return this._top(ws ? "Week of " + fmtDate(ws, { weekday: "long", day: "numeric", month: "long" }) : "This week", "Dinners",
        `<button class="pill filled" data-nav="/kitchen-planner/recipes"><ha-icon icon="mdi:book-open-variant"></ha-icon>Recipe book</button>`)
      + this._widgets()
      + `<div class="cols"><div class="col">
          <div class="gh">This week</div><div class="group">${rows}</div>
          <div class="gf">Tap a night to open the recipe book. Choose a recipe, then Add to meal plan.</div>${warn}
        </div><div class="col">
          <div class="gh">Leftovers to use up</div><div class="group">${lrows}</div>
          <div class="gf">${left.length ? "What the packs for this week's dinners will leave spare. Dinner suggestions that use them up are coming next." : "Pick some dinners and anything that would go to waste shows up here."}</div>
        </div></div>`;
  }

  _shop() {
    const la = this._a(KP.total, "line_aisles", {});
    const items = this._todo || [];
    const isStaple = (u) => u.startsWith("kp:staple:");
    const groups = {};
    for (const it of items) {
      const g = it.uid.startsWith("manual:") ? "Added by the family" : isStaple(it.uid) ? "Staples due" : (la[it.uid] || "Other");
      (groups[g] = groups[g] || []).push(it);
    }
    const order = Object.keys(groups).sort((a, b) => {
      const rank = (g) => (g === "Added by the family" ? 0 : g === "Staples due" ? 2 : 1);
      return rank(a) - rank(b) || a.localeCompare(b);
    });
    const row = (it) => {
      const on = it.status === "completed";
      const l = parseLine(it);
      return `<div class="row chk ${on ? "done" : ""}">
        <button class="check ${on ? "on" : ""}" data-check="${esc(it.uid)}" data-status="${on ? "needs_action" : "completed"}" aria-label="Tick">${on ? '<ha-icon icon="mdi:check"></ha-icon>' : ""}</button>
        <div class="rt"><div class="t">${esc(l.title)}</div>${l.sub ? `<div class="s">${esc(l.sub)}</div>` : ""}</div>
        ${l.value ? `<div class="rv">${esc(l.value)}</div>` : ""}
        <button class="x" data-remove="${esc(it.uid)}" aria-label="Remove"><ha-icon icon="mdi:close-circle"></ha-icon></button></div>`;
    };
    for (const g of Object.keys(groups)) groups[g].sort((a, b) => (a.status === "completed") - (b.status === "completed"));
    const left = [], right = [];
    order.forEach((g, i) => {
      const [icon, color] = AISLE_ICON[g] || (g === "Staples due" ? ["mdi:basket", "#FF9500"] : g === "Added by the family" ? ["mdi:account-heart", "#AF52DE"] : ["mdi:cart", "#8E8E93"]);
      const open = groups[g].filter((x) => x.status !== "completed").length;
      const html = `<div class="gh"><span><ha-icon icon="${icon}" style="--mdc-icon-size:14px;color:${color}"></ha-icon> ${esc(cap(g))}</span><span>${open} left</span></div>
        <div class="group">${groups[g].map(row).join("")}</div><div class="gf"></div>`;
      (g === "Staples due" || i % 2 ? right : left).push(html);
    });
    const ready = this._s(KP.ready);
    const isReady = ready && ready.state === "on";
    const floor = this._a(KP.total, "is_floor", false);
    const missing = [].concat(this._a(KP.total, "unpriced", []), this._a(KP.total, "no_pack_size", []));
    const toCheck = this._a(KP.staples, "to_check", []);
    return this._top(items.length + " items", "Shopping",
        `<button class="pill" data-refresh="1"><ha-icon icon="mdi:refresh"></ha-icon>Refresh</button>`)
      + this._widgets()
      + `<div class="cols"><div class="col">
          <div class="gh">Order</div>
          <div class="group">
            <div class="row icon"><div class="ri" style="background:var(--green)"><ha-icon icon="mdi:cart-check"></ha-icon></div>
              <div class="rt"><div class="t">Ready to send to Coles</div><div class="s">${isReady ? "Claude fills the trolley on Saturday" : "Turn on when the week is picked"}</div></div>
              <button class="sw ${isReady ? "on" : ""}" data-ready="1" aria-label="Order ready"></button></div>
            <div class="add"><ha-icon icon="mdi:plus-circle"></ha-icon><input id="newitem" placeholder="Add an item…" enterkeyhint="done"></div>
          </div>
          <div class="gf">${floor && missing.length ? "The estimate is a minimum: " + esc(missing.join(", ")) + " have no price on record." : "Tick items as they go in the trolley. Remove anything you don't need this week."}</div>
          ${left.join("")}
        </div><div class="col">${right.join("")}
          ${toCheck.length ? `<div class="gh">Maybe</div><div class="group">${toCheck.map((s) => `<div class="row"><div class="rt"><div class="t">${esc(s.label)}</div><div class="s">Bought irregularly. Add it above if you need it.</div></div></div>`).join("")}</div><div class="gf"></div>` : ""}
        </div></div>`;
  }

  _pantry() {
    const have = this._a(KP.house, "have", []);
    const notBuying = this._a(KP.house, "not_buying", []);
    const rows = PANTRY.map(([key, name, icon, color]) => {
      const st = this._s("switch.kitchen_planner_have_" + SWITCH[key]);
      const on = st && st.state === "on";
      const sub = on && st.attributes.until ? "One pack until " + fmtDate(st.attributes.until, { weekday: "short", day: "numeric", month: "short" }) : "Tap if you already have one";
      return `<div class="row icon"><div class="ri" style="background:${color}"><ha-icon icon="${icon}"></ha-icon></div>
        <div class="rt"><div class="t">${esc(name)}</div><div class="s">${esc(sub)}</div></div>
        <button class="sw ${on ? "on" : ""}" data-switch="${SWITCH[key]}" data-on="${on ? 1 : 0}" aria-label="${esc(name)}"></button></div>`;
    });
    const half = Math.ceil(rows.length / 2);
    const hrows = have.length ? have.map((h) => `<div class="row"><div class="rt"><div class="t">${esc(h.label)}</div>
        <div class="s">${h.until ? "Until " + esc(fmtDate(h.until, { weekday: "short", day: "numeric", month: "short" })) : '<span style="color:var(--orange)">No expiry: ask Claude to clear it once used</span>'}</div></div>
        <div class="rv">${esc(h.amount)}</div></div>`).join("")
      : `<div class="row"><div class="rt"><div class="t muted">Nothing recorded</div></div></div>`;
    return this._top("Takes it off this week's list", "In the house", "")
      + `<div class="cols"><div class="col"><div class="gh">Already got one?</div><div class="group">${rows.slice(0, half).join("")}</div><div class="gf"></div></div>
        <div class="col"><div class="gh">&nbsp;</div><div class="group">${rows.slice(half).join("")}</div>
        <div class="gf">Each switch takes one pack off this week's order and turns itself off after Sunday.</div></div></div>
        <div class="cols"><div class="col"><div class="gh">What the planner thinks we have</div><div class="group">${hrows}</div><div class="gf"></div></div>
        <div class="col"><div class="gh">Not buying any more</div><div class="group">${notBuying.length ? notBuying.map((n) => `<div class="row"><div class="rt"><div class="t">${esc(cap(n.label))}</div></div></div>`).join("") : '<div class="row"><div class="rt"><div class="t muted">Nothing</div></div></div>'}</div>
        <div class="gf">Never added to the order by the staples forecast.</div></div></div>`;
  }

  _spend() {
    const hist = this._a(KP.spend, "history", []);
    const avg = this._a(KP.spend, "average_last_8", null);
    const est = this._s(KP.total) ? this._s(KP.total).state : null;
    const last = hist.length ? hist[hist.length - 1] : null;
    const max = Math.max(50, ...hist.map((h) => h.total), Number(est) || 0) * 1.1;
    const W = 1000, H = 220, pad = 28, n = hist.length + 1, bw = Math.min(46, (W - pad) / n * 0.62);
    const x = (i) => pad + (i + 0.5) * ((W - pad) / n);
    const y = (v) => H - 22 - (v / max) * (H - 40);
    const step = hist.length > 16 ? 3 : hist.length > 8 ? 2 : 1;
    const bars = hist.map((h, i) => `<rect x="${x(i) - bw / 2}" y="${y(h.total)}" width="${bw}" height="${H - 22 - y(h.total)}" rx="${Math.min(8, bw / 2)}" fill="var(--blue)" opacity="${i === hist.length - 1 ? 1 : .55}"><title>${esc(h.date)} ${money(h.total)}</title></rect>
      ${i % step === (hist.length - 1) % step ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" font-size="16" fill="var(--label2)">${esc(fmtDate(h.date, { day: "numeric", month: "short" }).replace("Sept", "Sep"))}</text>` : ""}`).join("");
    const estBar = est && !isNaN(Number(est)) ? `<rect x="${x(hist.length) - bw / 2}" y="${y(Number(est))}" width="${bw}" height="${H - 22 - y(Number(est))}" rx="${Math.min(8, bw / 2)}" fill="none" stroke="var(--green)" stroke-width="3" stroke-dasharray="6 5"/>
      <text x="${x(hist.length)}" y="${H - 6}" text-anchor="middle" font-size="18" fill="var(--green)">Next</text>` : "";
    const avgLine = avg ? `<line x1="${pad}" x2="${W}" y1="${y(avg)}" y2="${y(avg)}" stroke="var(--orange)" stroke-width="2" stroke-dasharray="4 6"/>
      <rect x="${pad}" y="${y(avg) - 30}" width="150" height="24" rx="12" fill="var(--card)" opacity=".9"/><text x="${pad + 10}" y="${y(avg) - 12}" font-size="16" font-weight="600" fill="var(--orange)">Average ${money(avg)}</text>` : "";
    const recent = hist.slice(-6).reverse().map((h) => `<div class="row"><div class="rt"><div class="t">${esc(fmtDate(h.date, { weekday: "short", day: "numeric", month: "long" }))}</div></div><div class="rv">${money(h.total)}</div></div>`).join("");
    return this._top("Coles orders", "Spending", "")
      + `<div class="widgets">
          ${this._widget("mdi:receipt", "#007AFF", money(last && last.total), last ? "Last order · " + fmtDate(last.date, { day: "numeric", month: "short" }) : "Last order")}
          ${this._widget("mdi:chart-bell-curve", "#FF9500", money(avg), "Average of last 8")}
          ${this._widget("mdi:cart-arrow-right", "#34C759", money(est), "Next order estimate")}
          ${this._widget("mdi:counter", "#8E8E93", hist.length, "Orders on record")}
        </div>
        <div class="gh">Order totals</div>
        <div class="group chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${avgLine}${bars}${estBar}</svg></div>
        <div class="gf">The dashed green bar is the estimate for the next order. Specials and staple price trends are coming later.</div>
        <div class="cols"><div class="col"><div class="gh">Recent orders</div><div class="group">${recent || '<div class="row"><div class="rt"><div class="t muted">No orders yet</div></div></div>'}</div></div><div class="col"></div></div>`;
  }

  _render() {
    if (!this._hass || !this.shadowRoot) return;
    let body;
    if (!this._s(KP.dinners)) body = this._top("Kitchen", "Kitchen", "") + this._unavailable();
    else body = { week: () => this._week(), shop: () => this._shop(), pantry: () => this._pantry(), spend: () => this._spend() }[this._view]?.() || "";
    const focused = this.shadowRoot.activeElement && this.shadowRoot.activeElement.id === "newitem";
    const draft = focused ? this.shadowRoot.getElementById("newitem").value : null;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="page"><div class="wrap">${body}</div></div>`;
    if (focused) { const inp = this.shadowRoot.getElementById("newitem"); inp.value = draft; inp.focus(); }
    this._wire();
  }

  _wire() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-nav]").forEach((el) => el.addEventListener("click", () => this._nav(el.dataset.nav)));
    root.querySelectorAll("[data-check]").forEach((el) => el.addEventListener("click", async () => {
      await this._call("todo", "update_item", { item: el.dataset.check, status: el.dataset.status }, { entity_id: KP.todo });
    }));
    root.querySelectorAll("[data-remove]").forEach((el) => el.addEventListener("click", async () => {
      await this._call("todo", "remove_item", { item: el.dataset.remove }, { entity_id: KP.todo });
    }));
    root.querySelectorAll("[data-switch]").forEach((el) => el.addEventListener("click", () => {
      el.classList.toggle("on");
      this._call("switch", el.dataset.on === "1" ? "turn_off" : "turn_on", {}, { entity_id: "switch.kitchen_planner_have_" + el.dataset.switch });
    }));
    const ready = root.querySelector("[data-ready]");
    if (ready) ready.addEventListener("click", () => { ready.classList.toggle("on"); this._call("input_boolean", "toggle", {}, { entity_id: KP.ready }); });
    const refresh = root.querySelector("[data-refresh]");
    if (refresh) refresh.addEventListener("click", () => this._call("button", "press", {}, { entity_id: KP.refresh }));
    const inp = root.getElementById("newitem");
    if (inp) inp.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter" || !inp.value.trim()) return;
      const text = inp.value.trim(); inp.value = ""; inp.blur();
      await this._call("todo", "add_item", { item: text }, { entity_id: KP.todo });
    });
  }
}

if (!customElements.get("kitchen-planner-card")) customElements.define("kitchen-planner-card", KitchenPlannerCard);
window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "kitchen-planner-card"))
  window.customCards.push({ type: "kitchen-planner-card", name: "Kitchen Planner", description: "iOS-style kitchen views (week, shop, pantry, spend)" });
