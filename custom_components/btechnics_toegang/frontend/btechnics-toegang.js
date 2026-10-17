/*
 * Btechnics Toegang dashboardkaart, zelfde stijl als Btechnics VTO (Trefpunt).
 *   type: custom:btechnics-toegang
 *   view: deuren | personen | logboek
 * Gegevens via WebSocket btechnics_toegang/data, wijzigingen via de diensten (enkel beheerders).
 */
const D = "btechnics_toegang";
const C_OPEN = "#0288d1";
const C_REFUSED = "#db4437";
const C_OK = "#0b8043";
const MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
const DAYS = ["Zo", "Ma", "Di", "Wo", "Do", "Vr", "Za"];
const WEEK = [[1, "Ma"], [2, "Di"], [3, "Wo"], [4, "Do"], [5, "Vr"], [6, "Za"], [7, "Zo"]];
const MODI = [["open", "Beschikbaar", "mdi:door-open"], ["afwezig", "Niet aanwezig", "mdi:account-off-outline"],
  ["vergadering", "In vergadering", "mdi:account-group-outline"], ["niet_storen", "Niet storen", "mdi:minus-circle-outline"]];
const OPEN_DOMEINEN = ["script", "switch", "lock", "button", "input_button", "input_boolean"];
const SOORT = { toegang: "Toegang", geweigerd: "Geweigerd", fout: "Fout", alarm: "Alarm", systeem: "Systeem", oproep: "Oproep" };
const METHODE = { pin: "pincode", kaart: "kaart", gezicht: "gezicht", ha: "Home Assistant", knop: "knop" };
const REDEN = { onbekend: "onbekend", buiten_schema: "buiten de toegangsuren", verlopen: "niet meer geldig", fout: "fout" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const errText = (e) => (e && typeof e === "object" && e.message) ? e.message : (!e || typeof e === "number") ? "geen verbinding met Home Assistant" : String(e);
const isAdmin = (h) => !!(h && h.user && h.user.is_admin);

function formatters(tz) {
  const nf = new Intl.DateTimeFormat("en-US", { timeZone: tz || undefined, year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", hourCycle: "h23" });
  const parts = (d) => {
    const p = {};
    for (const x of nf.formatToParts(d)) if (x.type !== "literal") p[x.type] = Number(x.value);
    if (p.hour === 24) p.hour = 0;
    p.wd = new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay();
    return p;
  };
  const two = (n) => String(n).padStart(2, "0");
  const date = (p) => `${DAYS[p.wd]} ${p.day} ${MONTHS[p.month - 1]} ${p.year}`;
  const time = (p) => `${two(p.hour)}u${two(p.minute)}`;
  return {
    dateTime: (d) => { const p = parts(d); return `${date(p)}, ${time(p)}`; },
    time: (d) => time(parts(d)),
    compact: (d) => { const p = parts(d), n = parts(new Date());
      return p.year === n.year && p.month === n.month && p.day === n.day ? time(p) : `${p.day}/${p.month} ${time(p)}`; },
    day: (d) => { const p = parts(d); return `${p.year}-${two(p.month)}-${two(p.day)}`; },
    dayLabel: (d) => date(parts(d)),
  };
}
// "2026-10-12" als "Ma 12 okt 2026"
function datum(s) {
  if (!s) return "";
  const [y, m, d] = s.split("-").map(Number);
  return `${DAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ${d} ${MONTHS[m - 1]} ${y}`;
}
function dagenTekst(dagen) {
  const s = [...dagen].sort((a, b) => a - b);
  if (s.join() === "1,2,3,4,5,6,7") return "Elke dag";
  if (s.join() === "1,2,3,4,5") return "Ma tot vr";
  if (s.join() === "6,7") return "Weekend";
  const reeks = s.length > 2 && s.every((x, i) => i === 0 || x === s[i - 1] + 1);
  const n = (x) => WEEK[x - 1][1];
  return reeks ? `${n(s[0])} tot ${n(s[s.length - 1]).toLowerCase()}` : s.map(n).join(", ");
}
function schemaTekst(schema) {
  if (!schema || !schema.length) return "Altijd";
  return schema.map((b) => `${dagenTekst(b.dagen)} ${b.van.replace(":", "u")} tot ${b.tot.replace(":", "u")}${b.tot <= b.van ? " (volgende dag)" : ""}`).join("; ");
}

const CSS = `
  :host { display: block; min-width: 0; }
  ha-card { padding: 16px; }
  .muted { color: var(--secondary-text-color); }
  .title { font-size: 1.25rem; font-weight: 500; margin: 0 0 12px; color: var(--primary-text-color); }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .dot.open, .dot.on { background: ${C_OPEN}; } .dot.refused { background: ${C_REFUSED}; }
  .dot.off { background: var(--disabled-text-color, #9e9e9e); } .dot.blocked { background: var(--warning-color, #f4b400); }
  .dot.ok { background: ${C_OK}; }
  .status { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
  th { text-align: left; font-weight: 500; color: var(--secondary-text-color); padding: 8px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  td { padding: 8px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .mono { font-family: var(--code-font-family, ui-monospace, Menlo, Consolas, monospace); }
  .error { color: var(--error-color, ${C_REFUSED}); padding: 8px 0; }
  .empty { color: var(--secondary-text-color); padding: 16px 0; text-align: center; }
  input, select, button.btn { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; min-height: 40px; box-sizing: border-box; }
  button.btn { cursor: pointer; background: var(--secondary-background-color); display: inline-flex; align-items: center; gap: 6px; --mdc-icon-size: 18px; }
  button.btn:hover { border-color: var(--primary-color); }
  button.btn.active { border-color: var(--primary-color); color: var(--primary-color); }
  button.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  button.btn.danger { color: var(--error-color, ${C_REFUSED}); }
  button.btn:disabled { opacity: 0.5; cursor: default; }
  .scroll { overflow-x: auto; max-width: 100%; }
  .bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; align-items: center; }
  .bar input[type=search] { flex: 1 1 220px; min-width: 180px; }
  .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .chip { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; padding: 2px 8px; border-radius: 12px;
    background: var(--secondary-background-color); margin: 0 4px 4px 0; white-space: nowrap; }
  .acts { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
  .acts button.btn { min-height: 32px; padding: 4px 10px; font-size: 0.85rem; }
  .panel { border: 1px solid var(--divider-color); border-radius: 8px; padding: 12px; margin-bottom: 12px; display: none; }
  .panel.on { display: block; }
  .panel h3 { margin: 0 0 10px; font-size: 1rem; font-weight: 500; }
  .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 10px; }
  .row > label { display: inline-flex; align-items: center; gap: 6px; }
  .lbl { font-size: 0.85rem; color: var(--secondary-text-color); margin: 4px 0; }
  .msg { margin: 0 0 12px; font-size: 0.9rem; } .msg.ok { color: var(--success-color, ${C_OK}); }
  .hint { font-size: 0.8rem; color: var(--secondary-text-color); }
  button.reveal { background: none; border: 0; padding: 2px 4px; margin: -2px -4px; border-radius: 6px; cursor: pointer; font: inherit; color: inherit;
    display: inline-flex; align-items: center; gap: 6px; --mdc-icon-size: 16px; }
  button.reveal:hover { background: var(--secondary-background-color); }
  .hidden { letter-spacing: 2px; color: var(--secondary-text-color); }
  .days { display: inline-flex; gap: 4px; flex-wrap: wrap; }
  .days button { border: 1px solid var(--divider-color); background: none; border-radius: 16px; min-width: 38px; min-height: 34px; font: inherit; font-size: 0.85rem;
    cursor: pointer; color: var(--primary-text-color); }
  .days button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .blok { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 0; border-bottom: 1px dashed var(--divider-color); }
  .seg { display: inline-flex; flex-wrap: wrap; gap: 6px; }
  .seg button { border: 1px solid var(--divider-color); background: var(--secondary-background-color); border-radius: 20px; padding: 6px 12px; min-height: 36px;
    font: inherit; font-size: 0.9rem; cursor: pointer; color: var(--primary-text-color); display: inline-flex; gap: 6px; align-items: center; --mdc-icon-size: 18px; }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .deur { border: 1px solid var(--divider-color); border-radius: 12px; padding: 14px; margin-bottom: 12px; }
  .deur .kop { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: center; margin-bottom: 10px; }
  .deur .kop b { font-size: 1.1rem; font-weight: 500; }
  .deur .sub { margin: 12px 0 6px; font-size: 0.85rem; font-weight: 500; letter-spacing: 0.04em; text-transform: uppercase; color: var(--secondary-text-color); }
  .tab { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); flex-wrap: wrap; }
  .tab:last-child { border-bottom: 0; } .tab .mt { flex: 1; min-width: 160px; }
  .bigcode { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 14px; margin: 10px 0;
    border-radius: 12px; background: var(--secondary-background-color); }
  .bigcode .mono { font-size: 2rem; letter-spacing: 6px; font-weight: 500; }
  .bigcode small { color: var(--secondary-text-color); font-size: 0.85rem; text-align: center; }
  .mrow { display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 6px 2px; border: 0; border-bottom: 1px solid var(--divider-color);
    background: none; font: inherit; color: var(--primary-text-color); text-align: left; width: 100%; box-sizing: border-box; cursor: pointer; }
  .mrow .mt { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .mrow .mt b, .mrow .mt .s { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mrow .mt b { font-weight: 500; } .mrow .s { font-size: 0.85rem; color: var(--secondary-text-color); }
  .mrow .tm { width: 3.6em; flex: none; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; font-size: 0.9rem; }
  .more { display: flex; justify-content: center; padding: 12px 0 0; }
  td.when { white-space: nowrap; font-variant-numeric: tabular-nums; }
  .monly { display: none !important; } ha-card.m .monly { display: revert !important; } ha-card.m .donly { display: none !important; }
  ha-card.m { padding: 12px; }
  ha-card.m .shade:has(~ .panel.on) { display: block; position: fixed; inset: 0; z-index: 7; background: rgba(0, 0, 0, 0.45); }
  .shade { display: none; }
  ha-card.m .panel.on { position: fixed; left: 0; right: 0; bottom: 0; z-index: 8; margin: 0; max-height: 88vh; overflow-y: auto;
    background: var(--card-background-color, #fff); border: 0; border-radius: 16px 16px 0 0; padding: 6px 16px calc(16px + env(safe-area-inset-bottom, 0px));
    box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.25); box-sizing: border-box; }
  ha-card.m .panel.on::before { content: ""; display: block; width: 40px; height: 4px; border-radius: 2px; background: var(--divider-color); margin: 4px auto 10px; }
  ha-card.m .panel input, ha-card.m .panel select, ha-card.m .panel button.btn { min-height: 44px; }
  ha-card.m .bar { flex-wrap: nowrap; } ha-card.m .bar input[type=search] { min-width: 0; flex: 1 1 auto; min-height: 44px; }
  ha-card.m .tabs { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; margin: 0 -12px 8px; padding: 0 12px 2px; gap: 6px; }
  ha-card.m .tabs .btn { white-space: nowrap; border-radius: 20px; flex: none; }
  ha-card.m .tabs .btn.active { background: var(--primary-color); color: var(--text-primary-color, #fff); }
`;

class BtxToegang extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._show = false;
    this._revealed = new Set();
    this._q = "";
    this._tab = "all";
    this._form = null;
    this._limit = 50;
    this._codes = {};   // koppelcodes per deur: { code, tot }
  }
  setConfig(c) { this._config = { view: "personen", ...(c || {}) }; }
  static getStubConfig() { return { view: "personen" }; }
  getCardSize() { return 6; }
  getGridOptions() { return { columns: "full", min_columns: 6 }; }
  set hass(h) {
    this._hass = h;
    if (!this._fmt) this._fmt = formatters(h.config && h.config.time_zone);
    if (!h.user) return;
    if (this._built === undefined) { this._built = true; this._bouw(); }
  }
  get hass() { return this._hass; }
  connectedCallback() {
    if (this._built && !this._timer) { this._laad(); this._timer = setInterval(() => this._laad(), 15000); }
  }
  disconnectedCallback() { clearInterval(this._timer); this._timer = null; clearInterval(this._tik); }

  _ws(m) { return this._hass.callWS(m); }
  async _dienst(service, data, antw) {
    const r = await this._ws({ type: "call_service", domain: D, service, service_data: data, return_response: !!antw });
    return r && r.response;
  }
  get $() { return (id) => this.shadowRoot.getElementById(id); }

  _bouw() {
    const v = this._config.view;
    const titel = this._config.title || { deuren: "Deuren en tablets", personen: "Personen en pincodes", logboek: "Logboek" }[v] || "Toegang";
    const bar = v === "personen" ? `<div class="bar"><input id="q" type="search" placeholder="Zoek persoon" autocomplete="off">
        <button id="toggle" class="btn donly"><ha-icon icon="mdi:eye"></ha-icon> Codes tonen</button>
        <button id="nieuw" class="btn primary"><ha-icon icon="mdi:account-plus"></ha-icon> Nieuwe persoon</button></div><div class="tabs" id="tabs"></div>`
      : v === "deuren" ? `<div class="bar"><span class="muted" style="flex:1">Modus, openen en gekoppelde tablets per deur.</span>
        <button id="nieuw" class="btn primary"><ha-icon icon="mdi:door-closed"></ha-icon> Nieuwe deur</button></div>`
      : `<div class="bar"><input id="q" type="search" placeholder="Zoek persoon of tekst" autocomplete="off">
        <select id="fdeur"><option value="">Alle deuren</option></select>
        <select id="fsoort"><option value="">Alles</option><option value="toegang">Toegang</option><option value="geweigerd">Geweigerd</option>
          <option value="fout">Fout</option><option value="systeem">Systeem</option></select></div>`;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>
      <div class="title">${esc(titel)}</div>
      ${isAdmin(this._hass) ? `${bar}<div class="shade" id="shade"></div><div id="panel" class="panel"></div><div id="msg" class="msg"></div>
        <div id="out" class="scroll"><div class="muted">Laden...</div></div>` : `<div class="empty">Enkel beheerders kunnen de toegang beheren.</div>`}
    </ha-card>`;
    if (!isAdmin(this._hass)) return;
    const $ = this.$;
    this._watchMode();
    $("shade").addEventListener("click", () => { if (!this._busy) this._sluit(); });
    if ($("q")) $("q").addEventListener("input", (e) => { this._q = e.target.value.trim().toLowerCase(); this._limit = 50; this._render(); });
    if ($("toggle")) $("toggle").addEventListener("click", () => {
      this._show = !this._show; this._revealed.clear();
      $("toggle").innerHTML = `<ha-icon icon="${this._show ? "mdi:eye-off" : "mdi:eye"}"></ha-icon> ${this._show ? "Codes verbergen" : "Codes tonen"}`;
      this._render();
    });
    if ($("nieuw")) $("nieuw").addEventListener("click", () => v === "deuren" ? this._deurForm(null) : this._persoonForm(null));
    if ($("fdeur")) $("fdeur").addEventListener("change", () => { this._limit = 50; this._render(); });
    if ($("fsoort")) $("fsoort").addEventListener("change", () => { this._limit = 50; this._render(); });
    this.shadowRoot.addEventListener("click", (e) => this._klik(e));
    this.shadowRoot.addEventListener("change", (e) => this._wijzig(e));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && this._form && !this._busy) this._sluit(); });
    this._laad();
    if (this.isConnected && !this._timer) this._timer = setInterval(() => this._laad(), 15000);
    // koppelcode aftellen
    this._tik = setInterval(() => { if (Object.keys(this._codes).length && this._config.view === "deuren" && !this._form) this._render(); }, 1000);
  }
  _watchMode() {
    const apply = () => {
      const w = this.clientWidth || window.innerWidth;
      const m = w > 0 && w < 600;
      const c = this.shadowRoot.querySelector("ha-card");
      if (c) c.classList.toggle("m", m);
      if (m !== this._mobile) { this._mobile = m; if (this._data && !this._form) this._render(); }
    };
    apply();
    if (!this._ro) { this._ro = new ResizeObserver(apply); this._ro.observe(this); }
  }
  async _laad(force) {
    if (!force && (this._busy || this._form)) return;
    try {
      this._data = await this._ws({ type: `${D}/data`, log: 200 });
      if (!this._beheerders || Date.now() - this._beheerdersT > 300000) {
        try { this._beheerders = (await this._ws({ type: `${D}/beheerders` })).beheerders; this._beheerdersT = Date.now(); } catch (e) { this._beheerders = null; }
      }
      this._render();
    } catch (e) {
      const o = this.$("out");
      if (o) o.innerHTML = `<div class="error">Kon de gegevens niet laden: ${esc(errText(e))}</div>`;
    }
  }
  _bericht(t, ok) {
    const m = this.$("msg"); if (!m) return;
    m.className = "msg " + (ok ? "ok" : "error"); m.textContent = t || "";
    const p = this.$("panel");
    if (this._mobile && p && p.classList.contains("on")) {
      let s = p.querySelector(".smsg"); if (!s) { s = document.createElement("div"); p.prepend(s); }
      s.className = "smsg msg " + (ok ? "ok" : "error"); s.textContent = t || "";
    }
  }
  _sluit() { this._form = null; const p = this.$("panel"); p.classList.remove("on"); p.innerHTML = ""; this._render(); }
  _open(html) {
    // telkens een nieuw binnenblok: zo stapelen de luisteraars van vorige formulieren niet op
    const p = this.$("panel");
    p.innerHTML = "";
    const w = document.createElement("div");
    w.innerHTML = html;
    p.appendChild(w);
    p.classList.add("on");
    if (!this._mobile) p.scrollIntoView({ block: "nearest" });
    return w;
  }
  async _doe(fn, okTekst) {
    if (this._busy) return;
    this._busy = true;
    this._bericht("");
    this.shadowRoot.querySelectorAll("#panel button, .acts button, .deur button").forEach((b) => (b.disabled = true));
    try {
      const r = await fn();
      this._busy = false;
      if (okTekst) this._bericht(typeof okTekst === "function" ? okTekst(r) : okTekst, true);
      await this._laad(true);
      return r;
    } catch (e) {
      this._bericht(errText(e));
      this.shadowRoot.querySelectorAll("#panel button, .acts button, .deur button").forEach((b) => (b.disabled = false));
    } finally { this._busy = false; }
  }
  _render() {
    if (!this._data || !this.$("out")) return;
    const v = this._config.view;
    if (v === "deuren") this._renderDeuren(); else if (v === "personen") this._renderPersonen(); else this._renderLog();
  }

  /* ------------------------------------------------------------------ deuren */
  _entiteiten() {
    return Object.values(this._hass.states).filter((s) => OPEN_DOMEINEN.includes(s.entity_id.split(".")[0]))
      .map((s) => ({ id: s.entity_id, naam: s.attributes.friendly_name || s.entity_id }))
      .sort((a, b) => a.naam.localeCompare(b.naam, "nl"));
  }
  _cameras() {
    return Object.values(this._hass.states).filter((s) => s.entity_id.startsWith("camera."))
      .map((s) => ({ id: s.entity_id, naam: s.attributes.friendly_name || s.entity_id }))
      .sort((a, b) => a.naam.localeCompare(b.naam, "nl"));
  }
  _entNaam(id) { const s = id && this._hass.states[id]; return s ? (s.attributes.friendly_name || id) : id; }
  _renderDeuren() {
    const { deuren, tablets } = this._data;
    const f = this._fmt;
    const out = this.$("out");
    if (!deuren.length) { out.innerHTML = `<div class="empty">Nog geen deuren. Maak er een met Nieuwe deur.</div>`; return; }
    const nu = Date.now();
    for (const [k, c] of Object.entries(this._codes)) if (c.tot < nu) delete this._codes[k];
    out.innerHTML = deuren.map((d) => {
      const tb = tablets.filter((t) => t.deur_id === d.id);
      const c = this._codes[d.id];
      const rest = c ? Math.max(0, Math.round((c.tot - nu) / 1000)) : 0;
      return `<div class="deur">
        <div class="kop"><b>${esc(d.naam)}</b><span class="acts">
          <button class="btn" data-a="openen" data-id="${d.id}" ${d.open_entiteit ? "" : "disabled title=\"Kies eerst waarmee de deur opent\""}><ha-icon icon="mdi:door-open"></ha-icon> Deur openen</button>
          <button class="btn" data-a="deur" data-id="${d.id}"><ha-icon icon="mdi:pencil"></ha-icon> Aanpassen</button></span></div>
        <div class="seg">${MODI.map(([m, l, i]) => `<button class="${d.modus === m ? "on" : ""}" data-a="modus" data-id="${d.id}" data-m="${m}"><ha-icon icon="${i}"></ha-icon>${l}</button>`).join("")}</div>
        <div class="hint" style="margin-top:6px">Modus sinds ${esc(d.modus_sinds ? f.dateTime(new Date(d.modus_sinds)) : "?")}, gezet door ${d.modus_door === "ha" ? "Home Assistant" : esc((tablets.find((t) => t.id === d.modus_door) || {}).naam || "de tablet")}.
          Opent met: ${!d.open_entiteit && this._data.akuvox && this._data.akuvox.aan && this._data.akuvox.deur_id === d.id ? `<b>relais 1 van de Akuvox S532</b>${d.puls_s ? `, ${d.puls_s} s` : ""}` : d.open_entiteit ? `<b>${esc(this._entNaam(d.open_entiteit))}</b>${/^(switch|input_boolean|light)\./.test(d.open_entiteit) ? (d.puls_s ? `, gaat na ${d.puls_s} s weer uit` : ", blijft aan (pulsduur 0)") : ""}` : `<span class="error">nog niets gekozen</span>`}${d.camera ? ` · Foto: <b>${esc(this._entNaam(d.camera))}</b>` : ""}</div>
        ${(() => { const a = this._data.akuvox; if (!a || a.deur_id !== d.id) return "";
          const st = !a.aan ? "sync staat uit" : a.ok === null ? "nog niet gesynct" : a.ok ? "sync in orde" : `fout: ${esc(a.fout || "?")}`;
          return `<div class="sub">Akuvox S532 (${esc(a.ip)})</div><div class="hint"><span class="dot ${a.ok ? "on" : "off"}"></span> ${st}${a.aan ? ` · ${a.eigen} personen van HA op het toestel, ${a.toestel} gebruikers in totaal${a.vreemd_boven_start ? `, ${a.vreemd_boven_start} onbekende vanaf 700001 (blijven staan)` : ""}${a.laatst ? `, laatst ${esc(f.compact(new Date(a.laatst)))}` : ""}` : ""}</div>`; })()}
        <div class="sub">Tablets</div>
        ${tb.length ? tb.map((t) => `<div class="tab"><span class="dot ${t.online ? "on" : "off"}" title="${t.online ? "Online" : "Offline"}"></span>
            <span class="mt"><b>${esc(t.naam)}</b><div class="hint">${t.online ? "Online" : "Offline"}${t.laatst ? `, laatst gezien ${esc(f.compact(new Date(t.laatst)))}` : ""}${t.app_versie ? ` · app ${esc(t.app_versie)}` : ""}${t.status && t.status.schema ? ` · schema ${esc(String(t.status.schema))}` : ""}${t.status && t.status.log_wachtrij ? ` · ${t.status.log_wachtrij} logregels in wachtrij` : ""}</div>
              <div class="hint">${t.kanaal === "https" ? "Https" : t.kanaal === "http" ? `<span class="error">Nog via http</span>` : "Verbinding nog onbekend"}${t.laatste_lijst_ha ? ` · lijst opgehaald ${esc(f.compact(new Date(t.laatste_lijst_ha)))}` : ""}${t.status && t.status.laatste_lijst ? ` · geldige lijst op de tablet ${esc(f.compact(new Date(t.status.laatste_lijst)))}` : ""}</div>
              <div class="hint">${!t.gezicht_sleutel ? "Gezichten: nog geen sleutel" : t.status && typeof t.status.gezichten === "number" ? (t.status.gezichten === t.gezichten_verwacht ? `Gezichten in orde (${t.gezichten_verwacht})` : `<span class="error">Gezichten: ${t.status.gezichten} op de tablet, ${t.gezichten_verwacht} verwacht</span>`) : `Gezichten: ${t.gezichten_verwacht} verwacht`}${t.status && typeof t.status.personen === "number" ? ` · ${t.status.personen} personen op de tablet` : ""}</div></span>
            <span class="acts"><button class="btn" data-a="tabnaam" data-id="${t.id}"><ha-icon icon="mdi:pencil"></ha-icon> Naam</button><button class="btn danger" data-a="intrekken" data-id="${t.id}"><ha-icon icon="mdi:link-off"></ha-icon> Ontkoppelen</button></span></div>`).join("")
          : `<div class="hint">Nog geen tablet gekoppeld.</div>`}
        ${(() => { const meld = (this._data.log || []).filter((r) => r.deur_id === d.id).slice(0, 5);
          return meld.length ? `<div class="sub">Laatste meldingen van de tablet</div>${meld.map((r) => `<div class="hint">${esc(f.compact(new Date(r.tijd)))} · ${esc(SOORT[r.soort] || r.soort)}: ${esc(r.tekst || "")}</div>`).join("")}` : ""; })()}
        ${c ? `<div class="bigcode"><span class="mono">${esc(c.code)}</span><small>Koppelcode voor ${esc(d.naam)}. Geef ze in op de tablet onder Beheer, Koppelen.<br>${this._data.https && this._data.https.kort ? `Vingerafdruk https: <b class="mono">${esc(this._data.https.kort)}</b> (moet gelijk zijn aan wat de tablet toont)<br>` : ""}Nog ${Math.floor(rest / 60)}:${String(rest % 60).padStart(2, "0")} geldig, één keer bruikbaar.</small></div>`
          : `<div style="margin-top:10px"><button class="btn" data-a="koppel" data-id="${d.id}"><ha-icon icon="mdi:tablet"></ha-icon> Tablet koppelen</button></div>`}
      </div>`;
    }).join("") + (this._beheerders ? `<div class="deur"><div class="kop"><b>Beheerders</b></div>
      <div class="hint">Deze gebruikers van Home Assistant zien alle codes en kunnen ze delen. Elke keer dat iemand een code bekijkt of deelt, staat in de Wijzigingen bij Codes.</div>
      ${this._beheerders.map((b) => `<div class="tab"><span class="dot ${b.actief ? "on" : "off"}"></span><span class="mt"><b>${esc(b.naam || "?")}</b><div class="hint">${b.gebruikersnaam ? esc(b.gebruikersnaam) : "geen login met wachtwoord"}${b.eigenaar ? " · eigenaar" : ""}${b.actief ? "" : " · uitgeschakeld"}</div></span></div>`).join("")}
      <div class="hint">Beheerders beheer je in Home Assistant onder Instellingen, Personen, Gebruikers.</div></div>` : "");
  }
  _deurForm(d) {
    this._form = { type: "deur", id: d && d.id };
    const ents = this._entiteiten();
    const huidig = d && d.open_entiteit;
    const p = this._open(`<h3>${d ? `Deur ${esc(d.naam)} aanpassen` : "Nieuwe deur"}</h3>
      <div class="row"><label>Naam <input id="fnaam" type="text" value="${esc(d ? d.naam : "")}" placeholder="bv. Vergaderzaal" maxlength="40"></label></div>
      <div class="lbl">Deur openen met (script, schakelaar, slot of knop in Home Assistant)</div>
      <div class="row"><select id="fent" style="min-width:260px;max-width:100%"><option value="">Nog niet kiezen</option>
        ${huidig && !ents.find((x) => x.id === huidig) ? `<option value="${esc(huidig)}" selected>${esc(huidig)} (bestaat niet meer)</option>` : ""}
        ${ents.map((x) => `<option value="${esc(x.id)}" ${x.id === huidig ? "selected" : ""}>${esc(x.naam)} (${esc(x.id)})</option>`).join("")}</select></div>
      <div class="lbl">Pulsduur: een schakelaar of input_boolean gaat na zoveel seconden vanzelf weer uit (0 is niet terugzetten)</div>
      <div class="row"><input id="fpuls" type="number" min="0" max="60" step="1" value="${d && d.puls_s !== undefined ? d.puls_s : 5}" style="width:110px"> seconden</div>
      <div class="lbl">Camera voor de foto bij elke toegang (enkel zichtbaar voor beheerders)</div>
      <div class="row"><select id="fcam" style="min-width:260px;max-width:100%"><option value="">Geen foto</option>
        ${this._cameras().map((x) => `<option value="${esc(x.id)}" ${d && x.id === d.camera ? "selected" : ""}>${esc(x.naam)} (${esc(x.id)})</option>`).join("")}</select></div>
      <div class="row"><button class="btn primary" id="fbewaar">${d ? "Bewaren" : "Deur toevoegen"}</button><button class="btn" id="fannuleer">Annuleren</button>
        ${d ? `<span style="flex:1"></span><button class="btn danger" id="fweg"><ha-icon icon="mdi:delete"></ha-icon> Deur verwijderen</button>` : ""}</div>`);
    p.querySelector("#fannuleer").addEventListener("click", () => this._sluit());
    p.querySelector("#fbewaar").addEventListener("click", () => {
      const naam = p.querySelector("#fnaam").value.trim(), ent = p.querySelector("#fent").value;
      const puls = Math.max(0, Math.min(60, parseInt(p.querySelector("#fpuls").value, 10) || 0));
      const cam = p.querySelector("#fcam").value;
      if (!naam) return this._bericht("Geef de deur een naam.");
      this._doe(async () => {
        if (d) await this._dienst("deur_wijzigen", { deur: d.id, naam, open_entiteit: ent, puls_s: puls, camera: cam }, true);
        else {
          const r = await this._dienst("deur_toevoegen", ent ? { naam, open_entiteit: ent } : { naam }, true);
          const nid = r && r.deur && r.deur.id;
          if (nid && (puls !== 5 || cam)) await this._dienst("deur_wijzigen", { deur: nid, puls_s: puls, camera: cam }, true);
        }
        this._sluit();
      }, d ? `Deur ${naam} bewaard.` : `Deur ${naam} toegevoegd. Koppel nu een tablet.`);
    });
    const weg = p.querySelector("#fweg");
    if (weg) weg.addEventListener("click", () => {
      if (!confirm(`Deur ${d.naam} verwijderen? Personen verliezen hun recht op deze deur.`)) return;
      this._doe(async () => { await this._dienst("deur_verwijderen", { deur: d.id }); this._sluit(); }, `Deur ${d.naam} verwijderd.`);
    });
    p.querySelector("#fnaam").focus();
  }

  /* ---------------------------------------------------------------- personen */
  _status(p) { return !p.actief ? "blocked" : p.tot && p.tot < this._fmt.day(new Date()) ? "verlopen" : "actief"; }
  _renderPersonen() {
    const { personen, deuren } = this._data;
    const $ = this.$;
    const dn = Object.fromEntries(deuren.map((d) => [d.id, d.naam]));
    const tijdelijk = (p) => !!(p.van || p.tot);
    const tabs = [["all", "Alles", personen.length], ["actief", "Actief", personen.filter((p) => this._status(p) === "actief").length],
      ["tijdelijk", "Tijdelijk", personen.filter(tijdelijk).length], ["blocked", "Geblokkeerd", personen.filter((p) => !p.actief).length],
      ["verlopen", "Verlopen", personen.filter((p) => this._status(p) === "verlopen").length]];
    $("tabs").innerHTML = tabs.map(([v, l, n]) => `<button class="btn ${this._tab === v ? "active" : ""}" data-tab="${v}">${l} (${n})</button>`).join("");
    const q = this._q;
    const lijst = personen.filter((p) => (this._tab === "all" || (this._tab === "tijdelijk" ? tijdelijk(p) : this._status(p) === this._tab))
      && (!q || p.naam.toLowerCase().includes(q) || p.deuren.some((d) => (dn[d] || "").toLowerCase().includes(q))));
    const out = $("out");
    if (!personen.length) { out.innerHTML = `<div class="empty">Nog geen personen. ${deuren.length ? "Maak er een met Nieuwe persoon." : "Maak eerst een deur bij Deuren."}</div>`; return; }
    if (!lijst.length) { out.innerHTML = `<div class="empty">Niemand gevonden.</div>`; return; }
    const pin = (p) => !p.pin ? `<span class="muted">geen</span>` : (this._show || this._revealed.has(p.id))
      ? `<button class="reveal" data-reveal="${p.id}"><span class="mono">${esc(p.pin)}</span><ha-icon icon="mdi:eye-off"></ha-icon></button>`
      : `<button class="reveal" data-reveal="${p.id}" title="Code tonen"><span class="hidden">${"&bull;".repeat(p.pin.length)}</span><ha-icon icon="mdi:eye"></ha-icon></button>`;
    const st = (p) => { const s = this._status(p); return `<span class="status"><span class="dot ${s === "actief" ? "open" : s === "blocked" ? "blocked" : "off"}"></span>${s === "actief" ? "Actief" : s === "blocked" ? "Geblokkeerd" : "Verlopen"}</span>`; };
    const geldig = (p) => [p.van || p.tot ? `${p.van ? `vanaf ${datum(p.van)}` : ""}${p.van && p.tot ? " " : ""}${p.tot ? `tot ${datum(p.tot)}` : ""}` : "", schemaTekst(p.schema)].filter(Boolean).join("<br>");
    if (this._mobile) {
      out.innerHTML = `<div>${lijst.map((p) => `<button class="mrow" data-a="blad" data-id="${p.id}"><span class="dot ${this._status(p) === "actief" ? "open" : this._status(p) === "blocked" ? "blocked" : "off"}"></span>
        <span class="mt"><b>${esc(p.naam)}</b><span class="s">${esc(p.deuren.map((d) => dn[d] || "?").join(", ") || "geen deuren")} · ${esc(schemaTekst(p.schema))}</span></span>
        <ha-icon icon="mdi:chevron-right" class="muted"></ha-icon></button>`).join("")}</div>`;
      return;
    }
    out.innerHTML = `<table><thead><tr><th>Naam</th><th>Pincode</th><th>Deuren</th><th>Geldig</th><th>Status</th><th></th></tr></thead><tbody>
      ${lijst.map((p) => `<tr><td><b style="font-weight:500">${esc(p.naam)}</b></td><td>${pin(p)}</td>
        <td>${p.deuren.length ? p.deuren.map((d) => `<span class="chip">${esc(dn[d] || "?")}</span>`).join("") : `<span class="muted">geen</span>`}</td>
        <td class="muted" style="font-size:0.9rem">${geldig(p)}</td><td>${st(p)}</td>
        <td><div class="acts"><button class="btn" data-a="aanpassen" data-id="${p.id}">Aanpassen</button>
          <button class="btn" data-a="${p.actief ? "blokkeer" : "activeer"}" data-id="${p.id}">${p.actief ? "Blokkeren" : "Activeren"}</button>
          <button class="btn" data-a="historiek" data-id="${p.id}">Geschiedenis</button></div></td></tr>`).join("")}</tbody></table>`;
  }
  _blad(p) {
    this._form = { type: "blad", id: p.id };
    const dn = Object.fromEntries(this._data.deuren.map((d) => [d.id, d.naam]));
    const shown = this._show || this._revealed.has(p.id);
    const a = (act, icon, label, cls) => `<button class="mrow" data-a="${act}" data-id="${p.id}" style="${cls === "danger" ? "color:var(--error-color,#db4437)" : ""}"><ha-icon icon="${icon}"></ha-icon><span class="mt">${label}</span></button>`;
    this._open(`<h3>${esc(p.naam)}</h3><div class="hint">${esc(p.deuren.map((d) => dn[d] || "?").join(", ") || "geen deuren")} · ${esc(schemaTekst(p.schema))}</div>
      ${p.pin ? `<button class="bigcode" data-reveal="${p.id}" style="width:100%;border:0;cursor:pointer;font:inherit;color:inherit">${shown ? `<span class="mono">${esc(p.pin)}</span><small>tik om te verbergen</small>` : `<span class="hidden" style="font-size:1.6rem">${"&bull;".repeat(p.pin.length)}</span><small>tik om de code te tonen</small>`}</button>` : ""}
      ${a("aanpassen", "mdi:pencil", "Aanpassen")}${a(p.actief ? "blokkeer" : "activeer", p.actief ? "mdi:lock-clock" : "mdi:lock-open-variant", p.actief ? "Blokkeren" : "Activeren")}
      ${a("historiek", "mdi:history", "Geschiedenis")}${a("weg", "mdi:delete", "Verwijderen", "danger")}
      <button class="mrow" data-a="sluit"><ha-icon icon="mdi:close"></ha-icon><span class="mt">Sluiten</span></button>`);
  }
  _persoonForm(p) {
    const deuren = this._data.deuren;
    if (!deuren.length) return this._bericht("Maak eerst een deur bij Deuren.");
    this._form = { type: "persoon", id: p && p.id, blokken: (p ? p.schema : []).map((b) => ({ ...b, dagen: [...b.dagen] })) };
    const gekozen = new Set(p ? p.deuren : deuren.length === 1 ? [deuren[0].id] : []);
    const pa = this._open(`<h3>${p ? `${esc(p.naam)} aanpassen` : "Nieuwe persoon"}</h3>
      <div class="row"><label>Naam <input id="fnaam" type="text" value="${esc(p ? p.naam : "")}" maxlength="40" placeholder="Voornaam Naam"></label>
        <label>Pincode <input id="fpin" type="text" inputmode="numeric" class="mono" value="${esc(p && p.pin ? p.pin : "")}" maxlength="10" style="width:9em" placeholder="min. 4 cijfers"></label>
        <button class="btn" id="fwillekeurig" title="Willekeurige code van 6 cijfers"><ha-icon icon="mdi:dice-5"></ha-icon> Willekeurig</button></div>
      <div class="lbl">Deuren</div>
      <div class="row">${deuren.map((d) => `<label><input type="checkbox" data-deur="${d.id}" ${gekozen.has(d.id) ? "checked" : ""}> ${esc(d.naam)}</label>`).join("")}</div>
      <div class="lbl">Geldig (leeg laten is altijd)</div>
      <div class="row"><label>Vanaf <input id="fvan" type="date" value="${esc(p && p.van ? p.van : "")}"></label>
        <label>Tot en met <input id="ftot" type="date" value="${esc(p && p.tot ? p.tot : "")}"></label></div>
      <div class="lbl">Toegangsuren</div>
      <div id="fblokken"></div>
      <div class="row"><button class="btn" id="fblok"><ha-icon icon="mdi:clock-plus-outline"></ha-icon> Uren toevoegen</button>
        <span class="hint">Geen uren is altijd toegang. Een eindtijd voor de begintijd loopt door tot de volgende dag.</span></div>
      <div class="row" style="margin-top:6px"><button class="btn primary" id="fbewaar">${p ? "Bewaren" : "Persoon toevoegen"}</button><button class="btn" id="fannuleer">Annuleren</button>
        ${p ? `<span style="flex:1"></span><button class="btn danger" id="fweg"><ha-icon icon="mdi:delete"></ha-icon> Verwijderen</button>` : ""}</div>`);
    const tekenBlokken = () => {
      const el = pa.querySelector("#fblokken");
      const bl = this._form.blokken;
      el.innerHTML = bl.length ? bl.map((b, i) => `<div class="blok"><span class="days">${WEEK.map(([n, l]) => `<button class="${b.dagen.includes(n) ? "on" : ""}" data-blok="${i}" data-dag="${n}">${l}</button>`).join("")}</span>
          <label>van <input type="time" data-blok="${i}" data-veld="van" value="${b.van}"></label><label>tot <input type="time" data-blok="${i}" data-veld="tot" value="${b.tot}"></label>
          <button class="btn danger" data-blokweg="${i}" title="Weg"><ha-icon icon="mdi:close"></ha-icon></button></div>`).join("")
        : `<div class="hint" style="padding:4px 0 8px">Altijd toegang.</div>`;
    };
    tekenBlokken();
    pa.addEventListener("click", (e) => {
      const dag = e.target.closest("[data-dag]");
      if (dag) { const b = this._form.blokken[+dag.dataset.blok], n = +dag.dataset.dag;
        b.dagen = b.dagen.includes(n) ? b.dagen.filter((x) => x !== n) : [...b.dagen, n]; return tekenBlokken(); }
      const w = e.target.closest("[data-blokweg]");
      if (w) { this._form.blokken.splice(+w.dataset.blokweg, 1); return tekenBlokken(); }
    });
    pa.addEventListener("input", (e) => { const t = e.target; if (t.dataset && t.dataset.veld) this._form.blokken[+t.dataset.blok][t.dataset.veld] = t.value; });
    pa.querySelector("#fblok").addEventListener("click", () => { this._form.blokken.push({ dagen: [1, 2, 3, 4, 5], van: "08:00", tot: "18:00" }); tekenBlokken(); });
    pa.querySelector("#fwillekeurig").addEventListener("click", () => {
      const bezet = new Set(this._data.personen.map((x) => x.pin));
      let c; do { c = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, "0"); } while (bezet.has(c) || /^(\d)\1+$/.test(c));
      pa.querySelector("#fpin").value = c;
    });
    pa.querySelector("#fannuleer").addEventListener("click", () => this._sluit());
    pa.querySelector("#fbewaar").addEventListener("click", () => {
      const naam = pa.querySelector("#fnaam").value.trim();
      const pin = pa.querySelector("#fpin").value.replace(/\D/g, "");
      const ds = [...pa.querySelectorAll("[data-deur]")].filter((x) => x.checked).map((x) => x.dataset.deur);
      const van = pa.querySelector("#fvan").value || null, tot = pa.querySelector("#ftot").value || null;
      const blokken = this._form.blokken;
      if (!naam) return this._bericht("Geef een naam.");
      if (pin && pin.length < 4) return this._bericht("Een pincode heeft minstens 4 cijfers.");
      if (!pin && !p) return this._bericht("Geef een pincode of kies Willekeurig.");
      if (van && tot && tot < van) return this._bericht("De einddatum ligt voor de begindatum.");
      if (blokken.some((b) => !b.dagen.length || !b.van || !b.tot)) return this._bericht("Elk blok toegangsuren heeft minstens één dag, een begin en een einde.");
      const data = { deuren: ds, van, tot, schema: blokken };
      if (pin) data.pincode = pin;
      this._doe(async () => {
        if (p) await this._dienst("persoon_wijzigen", { persoon: p.id, nieuwe_naam: naam, ...data }, true);
        else await this._dienst("persoon_toevoegen", { naam, ...data }, true);
        this._sluit();
      }, p ? `${naam} bewaard.` : `${naam} toegevoegd.${ds.length ? "" : " Let op: nog geen deur gekozen."}`);
    });
    const weg = pa.querySelector("#fweg");
    if (weg) weg.addEventListener("click", () => this._verwijder(p));
    pa.querySelector("#fnaam").focus();
  }
  _verwijder(p) {
    if (!confirm(`${p.naam} verwijderen? De pincode werkt dan nergens meer.`)) return;
    this._doe(async () => { await this._dienst("persoon_verwijderen", { persoon: p.id }); this._sluit(); }, `${p.naam} verwijderd.`);
  }
  _historiek(p) {
    this._form = { type: "historiek", id: p.id };
    const rijen = this._data.log.filter((r) => r.persoon_id === p.id);
    const f = this._fmt;
    const ok = rijen.filter((r) => r.soort === "toegang").length;
    const pa = this._open(`<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><h3>Geschiedenis: ${esc(p.naam)}</h3><button class="btn" id="hsluit">Sluiten</button></div>
      <div class="hint" style="margin-bottom:8px">${rijen.length} regels, ${ok} toegang, ${rijen.length - ok} andere.</div>
      ${rijen.length ? this._logLijst(rijen.slice(0, 100), false) : `<div class="empty">Nog niets in het logboek.</div>`}`);
    pa.querySelector("#hsluit").addEventListener("click", () => this._sluit());
  }

  /* ---------------------------------------------------------------- logboek */
  _logLijst(rijen, metPersoon) {
    const f = this._fmt;
    const wat = (r) => [SOORT[r.soort] || r.soort, r.methode ? `met ${METHODE[r.methode] || r.methode}` : "", r.reden ? `(${REDEN[r.reden] || r.reden})` : ""].filter(Boolean).join(" ");
    const dot = (r) => r.soort === "toegang" ? "open" : r.soort === "geweigerd" || r.soort === "fout" || r.soort === "alarm" ? "refused" : "off";
    const naam = (r) => r.persoon || (r.soort === "geweigerd" ? "Onbekend" : "");
    if (this._mobile) {
      return rijen.map((r) => `<div class="mrow" style="cursor:default" title="${esc(r.tekst || "")}"><span class="tm">${esc(f.compact(new Date(r.tijd)))}</span>
        <span class="mt"><b>${esc((metPersoon ? naam(r) : r.deur) || r.deur || "")}</b><span class="s">${esc([metPersoon ? r.deur : "", wat(r)].filter(Boolean).join(" · "))}</span></span>
        <span class="dot ${dot(r)}"></span></div>`).join("");
    }
    return `<table><thead><tr><th>Wanneer</th><th>Deur</th>${metPersoon ? "<th>Persoon</th>" : ""}<th>Wat</th><th>Tekst</th></tr></thead><tbody>
      ${rijen.map((r) => `<tr><td class="when">${esc(f.dateTime(new Date(r.tijd)))}</td><td>${esc(r.deur || "")}</td>${metPersoon ? `<td>${esc(naam(r))}</td>` : ""}
        <td><span class="status"><span class="dot ${dot(r)}"></span>${esc(wat(r))}</span></td><td class="muted" style="font-size:0.9rem">${esc(r.tekst || "")}</td></tr>`).join("")}</tbody></table>`;
  }
  _renderLog() {
    const $ = this.$;
    const { log, deuren } = this._data;
    const sel = $("fdeur");
    const keuze = sel.value;
    sel.innerHTML = `<option value="">Alle deuren</option>` + deuren.map((d) => `<option value="${d.id}" ${d.id === keuze ? "selected" : ""}>${esc(d.naam)}</option>`).join("");
    const soort = $("fsoort").value, q = this._q;
    const rijen = log.filter((r) => (!keuze || r.deur_id === keuze) && (!soort || r.soort === soort)
      && (!q || (r.persoon || "").toLowerCase().includes(q) || (r.tekst || "").toLowerCase().includes(q) || (r.deur || "").toLowerCase().includes(q)));
    const out = $("out");
    if (!rijen.length) { out.innerHTML = `<div class="empty">${log.length ? "Niets gevonden." : "Nog niets in het logboek."}</div>`; return; }
    out.innerHTML = this._logLijst(rijen.slice(0, this._limit), true)
      + (rijen.length > this._limit ? `<div class="more"><button class="btn" data-a="meer">Meer tonen (${this._limit} van ${rijen.length})</button></div>` : "");
  }

  /* ----------------------------------------------------------------- acties */
  _klik(e) {
    const t = e.target.closest("[data-tab]");
    if (t) { this._tab = t.dataset.tab; return this._render(); }
    const rv = e.target.closest("[data-reveal]");
    if (rv) {
      const id = rv.dataset.reveal;
      if (this._revealed.has(id)) this._revealed.delete(id); else this._revealed.add(id);
      if (this._form && this._form.type === "blad") this._blad(this._data.personen.find((p) => p.id === id));
      return this._render();
    }
    const b = e.target.closest("[data-a]");
    if (!b || b.disabled) return;
    const id = b.dataset.id, a = b.dataset.a;
    const p = this._data && this._data.personen.find((x) => x.id === id);
    const d = this._data && this._data.deuren.find((x) => x.id === id);
    switch (a) {
      case "meer": this._limit += 100; return this._render();
      case "sluit": return this._sluit();
      case "blad": return this._blad(p);
      case "aanpassen": return this._persoonForm(p);
      case "historiek": return this._historiek(p);
      case "weg": return this._verwijder(p);
      case "blokkeer": case "activeer":
        return this._doe(async () => { await this._dienst("persoon_wijzigen", { persoon: p.id, actief: a === "activeer" }, true); if (this._form) this._sluit(); },
          `${p.naam} ${a === "activeer" ? "is weer actief" : "is geblokkeerd"}.`);
      case "deur": return this._deurForm(d);
      case "modus": return this._doe(() => this._dienst("modus_zetten", { deur: id, modus: b.dataset.m }), `Modus van ${d.naam} gezet.`);
      case "openen":
        if (!confirm(`Deur ${d.naam} nu openen?`)) return;
        return this._doe(() => this._dienst("deur_openen", { deur: id }), `Deur ${d.naam} geopend.`);
      case "koppel":
        return this._doe(async () => {
          const r = await this._dienst("tablet_koppelcode", { deur: id }, true);
          this._codes[id] = { code: r.code, tot: Date.now() + 10 * 60 * 1000 };
        }, `Koppelcode gemaakt voor ${d.naam}.`);
      case "tabnaam": {
        const tb = this._data.tablets.find((x) => x.id === id);
        const naam = (prompt("Nieuwe naam voor deze tablet", tb.naam) || "").trim();
        if (!naam || naam === tb.naam) return;
        return this._doe(() => this._dienst("tablet_hernoemen", { tablet: id, naam }), `Tablet heet nu ${naam}.`);
      }
      case "intrekken": {
        const tb = this._data.tablets.find((x) => x.id === id);
        if (!confirm(`${tb.naam} ontkoppelen? De tablet wist dan zijn lijst en ALLE gezichten, en toont Niet gekoppeld.`)) return;
        return this._doe(() => this._dienst("tablet_intrekken", { tablet: id }), `${tb.naam} ontkoppeld.`);
      }
    }
  }
  _wijzig() {}
}

// Registreren, en opnieuw proberen als de frontend nadien een eigen kaartregister plaatst.
function registreer() {
  if (customElements.get("btechnics-toegang")) return;
  try { customElements.define("btechnics-toegang", class extends BtxToegang {}); } catch (e) { /* al gekend */ }
}
registreer();
for (const ms of [0, 500, 2000, 5000]) setTimeout(registreer, ms);
window.addEventListener("location-changed", () => setTimeout(registreer, 0));
window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "btechnics-toegang"))
  window.customCards.push({ type: "btechnics-toegang", name: "Btechnics Toegang", description: "Deuren, personen en logboek van de deurtablets (view: deuren, personen of logboek)." });
