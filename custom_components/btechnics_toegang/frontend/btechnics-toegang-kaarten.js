/*
 * Btechnics Toegang dashboardkaarten (overgenomen van Btechnics VTO, Trefpunt, en aangepast voor de deurtablets)
 *   custom:btechnics-toegang-overzicht   status van alle deuren
 *   custom:btechnics-toegang-historiek   toegangshistoriek: zoeken, filteren, per persoon, per maand, CSV
 *   custom:btechnics-toegang-codes       codes per persoon, tijdelijke codes, uitnodigingen (beheerders; gebruikers enkel eigen tijdelijke codes)
 *   custom:btechnics-toegang-handleiding uitleg
 * Gegevens komen via de WebSocket-commando's van de integratie; tijden in de tijdzone van Home Assistant.
 */
const C_OPEN = "#0288d1";
const C_REFUSED = "#db4437";
const MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// Label voor een toegang zonder naam (door de integratie gezet volgens de methode), geen persoon.
const LABEL_RE = /^(Op afstand|Exitknop|Onbekende badge|Onbekend gezicht|Foute code|Onbekende code)$/;
const isLabel = (n) => LABEL_RE.test(String(n || ""));

function errText(e) {
  if (e && typeof e === "object" && e.message) return e.message;
  if (typeof e === "number" || !e) return "geen verbinding met Home Assistant";
  return String(e);
}

const DAYS = ["Zo", "Ma", "Di", "Wo", "Do", "Vr", "Za"];
const DAYS_LONG = ["zondag", "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag"];
const MONTHS_LONG = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"];

// Datums als "Za 13 sep 2026" en uren als "23u14", altijd in de tijdzone van Home Assistant.
function formatters(tz) {
  const nf = new Intl.DateTimeFormat("en-US", { timeZone: tz || undefined, year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric", hourCycle: "h23" });
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
    dateTime: { format: (d) => { const p = parts(d); return `${date(p)}, ${time(p)}`; } },
    date: { format: (d) => date(parts(d)) },
    // voluit, voor berichten: "maandag 28 september 2026"
    longDate: { format: (d) => { const p = parts(d); return `${DAYS_LONG[p.wd]} ${p.day} ${MONTHS_LONG[p.month - 1]} ${p.year}`; } },
    time: { format: (d) => time(parts(d)) },
    short: { format: (d) => { const p = parts(d); return `${date(p)}, ${time(p)}`; } },
    // zonder jaartal: "Ma 28 sep, 09u26"
    noYear: { format: (d) => { const p = parts(d); return `${DAYS[p.wd]} ${p.day} ${MONTHS[p.month - 1]}, ${time(p)}`; } },
    // kort voor smalle kolommen: vandaag enkel het uur, anders "27/9 18u49"
    compact: { format: (d) => { const p = parts(d), n = parts(new Date());
      return p.year === n.year && p.month === n.month && p.day === n.day ? time(p) : `${p.day}/${p.month} ${time(p)}`; } },
    // CSV: gewone notatie die Excel herkent
    csvDate: { format: (d) => { const p = parts(d); return `${two(p.day)}/${two(p.month)}/${p.year}`; } },
    csvTime: { format: (d) => { const p = parts(d); return `${two(p.hour)}:${two(p.minute)}:${two(p.second)}`; } },
  };
}

/* Foto's bij een toegang: enkel voor beheerders, via een ondertekend pad (een <img> kan geen token meesturen). */
const PHOTO_SRC = new Map();
async function photoSrc(hass, id) {
  const hit = PHOTO_SRC.get(id);
  if (hit && hit.until > Date.now()) return hit.path;
  const r = await hass.callWS({ type: "auth/sign_path", path: `/api/btechnics_toegang/foto/${id}`, expires: 3600 });
  PHOTO_SRC.set(id, { path: r.path, until: Date.now() + 3000 * 1000 });
  return r.path;
}
function isAdmin(hass) {
  return !!(hass && hass.user && hass.user.is_admin);
}
// icoon per methode (volledige tekst in de tooltip)
function methodIcon(m) {
  const t = String(m || "").toLowerCase();
  const icon = t.startsWith("op afstand") ? "mdi:cellphone-link" : t.startsWith("binnenpost") ? "mdi:deskphone"
    : t.startsWith("badge") ? "mdi:card-account-details-outline" : t.startsWith("vingerafdruk") ? "mdi:fingerprint"
    : t.startsWith("gezicht") ? "mdi:face-recognition" : t.startsWith("exitknop") ? "mdi:gesture-tap-button"
    : t.startsWith("code") || t.startsWith("ongeldige") ? "mdi:dialpad" : "mdi:help-circle-outline";
  return `<ha-icon icon="${icon}" title="${esc(m)}" aria-label="${esc(m)}"></ha-icon>`;
}
function photoCell(hass, x, cap) {
  return isAdmin(hass) ? `<td class="ph">${photoBtn(hass, x, cap)}</td>` : "";
}
function photoBtn(hass, x, cap) {
  if (!x.photo || !isAdmin(hass)) return "";
  return `<button class="photo" data-photo="${esc(x.photo)}" data-cap="${esc(cap)}" title="Foto bekijken" aria-label="Foto bekijken"><ha-icon icon="mdi:camera"></ha-icon></button>`;
}
// een toegang als rij voor de gsm: uur, naam, deur en manier, foto of status
function accessRow(hass, f, x, o) {
  const d = new Date(x.ts * 1000);
  const nm = x.name === "?" ? "Onbekende code" : x.name;
  const lbl = x.name === "?" || isLabel(x.name);
  const sub = [o.door ? x.door : "", x.method + (x.card ? ` ${x.card}` : "")].filter(Boolean).join(" · ");
  const ph = photoBtn(hass, x, `${nm}, ${x.door}, ${f.dateTime.format(d)}`);
  return `<div class="mrow" ${o.person === false ? "" : `role="button" tabindex="0" data-person="${esc(x.name)}"`} title="${esc(`${nm}\n${f.dateTime.format(d)}\n${x.door}, ${x.method}\n${x.opened ? "Geopend" : "Geweigerd"}`)}">
    <span class="tm">${esc(o.date ? f.compact.format(d) : f.time.format(d))}</span>
    <span class="mt"><b class="${lbl ? "muted" : ""}">${esc(nm)}</b><span class="sub">${esc(sub)}${x.opened ? "" : ' · <span class="refusedtxt">geweigerd</span>'}</span></span>
    ${ph || `<span class="dotbox"><span class="dot ${x.opened ? "open" : "refused"}" title="${x.opened ? "Geopend" : "Geweigerd"}"></span></span>`}</div>`;
}
async function openPhoto(root, hass, id, cap) {
  closePhoto(root);
  const lb = document.createElement("div");
  lb.className = "lb";
  lb.innerHTML = `<figure><div class="lbimg muted">Foto laden...</div><figcaption>${esc(cap || "")}</figcaption>
    <button class="btn" id="lbclose">Sluiten</button></figure>`;
  lb.addEventListener("click", (e) => { if (e.target === lb || e.target.id === "lbclose") closePhoto(root); });
  root.appendChild(lb);
  root._lbKey = (e) => { if (e.key === "Escape") closePhoto(root); };
  document.addEventListener("keydown", root._lbKey);
  try {
    const src = await photoSrc(hass, id);
    const img = new Image();
    img.alt = cap || "Foto";
    img.onload = () => { const box = lb.querySelector(".lbimg"); if (box) box.replaceWith(img); };
    img.onerror = () => { const box = lb.querySelector(".lbimg"); if (box) box.textContent = "Foto niet (meer) beschikbaar."; };
    img.src = src;
  } catch (e) {
    const box = lb.querySelector(".lbimg");
    if (box) box.textContent = `Foto laden mislukt: ${errText(e)}`;
  }
}
function closePhoto(root) {
  const lb = root.querySelector(".lb");
  if (lb) lb.remove();
  if (root._lbKey) { document.removeEventListener("keydown", root._lbKey); root._lbKey = null; }
}

const BASE_CSS = `
  [hidden] { display: none !important; }
  :host { display: block; min-width: 0; }
  ha-card { padding: 16px; }
  .muted { color: var(--secondary-text-color); }
  .title { font-size: 1.25rem; font-weight: 500; margin: 0 0 12px; color: var(--primary-text-color); }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .dot.open { background: ${C_OPEN}; }
  .dot.refused { background: ${C_REFUSED}; }
  .status { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
  th { text-align: left; font-weight: 500; color: var(--secondary-text-color); padding: 8px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  td { padding: 8px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .refusedtxt { color: var(--error-color, #db4437); font-weight: 500; }
  .mono { font-family: var(--code-font-family, ui-monospace, Menlo, Consolas, monospace); }
  .link { color: var(--primary-color); cursor: pointer; background: none; border: none; padding: 0; font: inherit; text-align: left; }
  .link:hover { text-decoration: underline; }
  .error { color: var(--error-color, ${C_REFUSED}); padding: 8px 0; }
  .empty { color: var(--secondary-text-color); padding: 16px 0; text-align: center; }
  input, select, button.btn {
    font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; min-height: 40px; box-sizing: border-box;
  }
  button.btn { cursor: pointer; background: var(--secondary-background-color); }
  button.btn:hover { border-color: var(--primary-color); }
  button.btn.active { border-color: var(--primary-color); color: var(--primary-color); }
  .scroll { overflow-x: auto; max-width: 100%; }
  button.photo { background: none; border: 0; padding: 0 2px; cursor: pointer; color: var(--secondary-text-color); --mdc-icon-size: 18px; vertical-align: middle; }
  button.photo:hover, button.photo:focus-visible { color: var(--primary-color); }
  th.ph, td.ph { width: 32px; text-align: center; padding-left: 2px; padding-right: 2px; color: var(--secondary-text-color); --mdc-icon-size: 18px; }
  .lb { position: fixed; inset: 0; z-index: 10; background: rgba(0, 0, 0, 0.75); display: flex; align-items: center; justify-content: center; padding: 16px; }
  .lb figure { margin: 0; background: var(--card-background-color, #fff); border-radius: 12px; padding: 12px; max-width: min(920px, 100%);
    display: flex; flex-direction: column; gap: 8px; align-items: center; }
  .lb img { max-width: 100%; max-height: 75vh; border-radius: 8px; display: block; }
  .lb .lbimg { min-width: 240px; min-height: 120px; display: flex; align-items: center; justify-content: center; }
  .lb figcaption { color: var(--primary-text-color); font-size: 0.95rem; text-align: center; }
  button.thumb { border: 0; padding: 0; background: none; cursor: pointer; flex: none; }
  button.thumb img { width: 88px; height: 50px; object-fit: cover; border-radius: 8px; display: block; }
  /* ---- gsm (kaart smaller dan 600 px): ha-card krijgt klasse m ---- */
  .monly { display: none !important; }
  ha-card.m .monly { display: revert !important; }
  ha-card.m .donly { display: none !important; }
  ha-card.m { padding: 12px; }
  button.fab { display: none; position: fixed; right: 16px; bottom: calc(18px + env(safe-area-inset-bottom, 0px)); z-index: 6;
    align-items: center; gap: 8px; border: 0; border-radius: 28px; padding: 14px 20px; font: inherit; font-size: 1rem; font-weight: 500;
    background: #ED6928; color: #fff; box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3); cursor: pointer; --mdc-icon-size: 20px; }
  ha-card.m button.fab { display: inline-flex; }
  ha-card.m:has(.sheet.on) button.fab { display: none; }
  ha-card.m .fabspace { height: 72px; }
  .shade { display: none; }
  ha-card.m .shade:has(~ .sheet.on) { display: block; position: fixed; inset: 0; z-index: 7; background: rgba(0, 0, 0, 0.45); }
  ha-card.m .sheet.on { position: fixed; left: 0; right: 0; bottom: 0; z-index: 8; margin: 0; max-height: 88vh; overflow-y: auto;
    background: var(--card-background-color, #fff); border: 0; border-radius: 16px 16px 0 0; padding: 6px 16px calc(16px + env(safe-area-inset-bottom, 0px));
    box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.25); box-sizing: border-box; }
  ha-card.m .sheet.on::before { content: ""; display: block; width: 40px; height: 4px; border-radius: 2px; background: var(--divider-color); margin: 4px auto 10px; }
  ha-card.m .sheet button.btn, ha-card.m .sheet a.btn, ha-card.m .sheet input, ha-card.m .sheet select { min-height: 44px; }
  .mlist { display: flex; flex-direction: column; }
  .mrow { display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 6px 2px; border: 0; border-bottom: 1px solid var(--divider-color);
    background: none; font: inherit; color: var(--primary-text-color); text-align: left; width: 100%; box-sizing: border-box; cursor: pointer; }
  .mrow:last-child { border-bottom: 0; }
  .mrow .mt { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .mrow .mt b, .mrow .mt .sub { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mrow .mt b { font-weight: 500; }
  .mrow .sub { font-size: 0.85rem; color: var(--secondary-text-color); }
  .mrow .tm { width: 3.2em; flex: none; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; font-size: 0.9rem; }
  .mrow > ha-icon { color: var(--secondary-text-color); flex: none; --mdc-icon-size: 20px; }
  .mrow button.photo { width: 40px; height: 40px; border-radius: 10px; background: var(--secondary-background-color); padding: 0; flex: none;
    display: inline-flex; align-items: center; justify-content: center; --mdc-icon-size: 20px; }
  .mrow .dotbox { width: 40px; flex: none; display: flex; justify-content: center; }
  .mday { font-size: 0.78rem; font-weight: 500; letter-spacing: 0.04em; text-transform: uppercase; color: var(--secondary-text-color); margin: 14px 2px 2px; }
  .mday:first-child { margin-top: 2px; }
  button.fold { display: flex; justify-content: space-between; align-items: center; width: 100%; min-height: 44px; font: inherit; color: var(--primary-text-color);
    background: var(--secondary-background-color); border: 0; border-radius: 10px; padding: 8px 12px; cursor: pointer; margin: 4px 0 12px; }
  button.fold span { color: var(--secondary-text-color); font-size: 0.9rem; }
  .act { display: flex; align-items: center; gap: 14px; width: 100%; min-height: 50px; border: 0; border-bottom: 1px solid var(--divider-color); background: none;
    font: inherit; font-size: 1rem; color: var(--primary-text-color); text-align: left; cursor: pointer; padding: 0 4px; --mdc-icon-size: 22px; }
  .act ha-icon { color: var(--secondary-text-color); }
  .act:last-child { border-bottom: 0; }
  .act.danger, .act.danger ha-icon { color: var(--error-color, #db4437); }
  .act.primary, .act.primary ha-icon { color: var(--primary-color); font-weight: 500; }
  .actsep { height: 8px; background: var(--secondary-background-color); margin: 4px -16px; }
  .shead { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
  .shead .mt { flex: 1; min-width: 0; } .shead b { font-size: 1.1rem; font-weight: 500; display: block; }
  .av { width: 38px; height: 38px; border-radius: 50%; background: var(--secondary-background-color); color: var(--primary-text-color);
    display: flex; align-items: center; justify-content: center; font-weight: 500; flex: none; }
`;
// naar een andere pagina van dit dashboard, met een opdracht voor de kaart daar (bv. meteen een tijdelijke code)
function goView(view, intent) {
  window.__btxtoegIntent = intent ? { ...intent, view, at: Date.now() } : null;
  const base = location.pathname.split("/").slice(0, 2).join("/");
  history.pushState(null, "", `${base}/${view}`);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
}
function takeIntent(view) {
  const i = window.__btxtoegIntent;
  if (!i || i.view !== view || Date.now() - i.at > 15000) return null;
  window.__btxtoegIntent = null;
  return i;
}
const FAB = `<button class="fab" id="fab" title="Tijdelijke code maken"><ha-icon icon="mdi:timer-plus-outline"></ha-icon> Tijdelijke code</button>`;
// toegangsuren (weekschema) zoals de tablets ze kennen: dagen 1 = maandag tot 7 = zondag
const WEEK = [[1, "Ma"], [2, "Di"], [3, "Wo"], [4, "Do"], [5, "Vr"], [6, "Za"], [7, "Zo"]];
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
/* Delen: WhatsApp, sms, mail, kopiëren en deelmenu. In de HA app (iPhone en Android) opent elke knop de deelpagina
   in de gewone browser, want de app zelf laat sms, mail en het klembord niet altijd toe. */
function inHaApp(hass) {
  // de HA apps (iPhone, iPad, Mac en Android) zetten "Home Assistant" of "HomeAssistant" in hun user agent
  return !!(/Home ?Assistant/i.test(navigator.userAgent) || (hass && hass.auth && hass.auth.external) || window.externalApp
    || (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.externalBus));
}
const APPLE = /iPhone|iPad|iPod|Macintosh/.test(navigator.userAgent);
function deelLinks(tekst, onderwerp, app) {
  const t = encodeURIComponent(tekst);
  if (app) {
    const basis = `${location.origin}/api/btechnics_toegang/deel#s=${encodeURIComponent(onderwerp)}&b=${t}&w=`;
    return { wa: basis + "wa", sms: basis + "sms", mail: basis + "mail", menu: basis + "menu" };
  }
  return { wa: `https://wa.me/?text=${t}`, sms: (APPLE ? "sms:&body=" : "sms:?body=") + t,
    mail: `mailto:?subject=${encodeURIComponent(onderwerp)}&body=${t}`, menu: null };
}
async function kopieer(tekst, veld) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(tekst); return true; } catch (err) { /* verder met de oude manier */ }
  }
  // oude manier: tijdelijk tekstvak buiten de shadow DOM (werkt ook in Safari en de Android app)
  const el = document.createElement("textarea");
  el.value = tekst; el.setAttribute("readonly", ""); el.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;";
  document.body.appendChild(el);
  el.focus(); el.select(); el.setSelectionRange(0, tekst.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
  el.remove();
  if (!ok && veld) { veld.focus(); veld.select(); veld.setSelectionRange(0, tekst.length); }
  return ok;
}
function deelKnoppenHtml() {
  return `<a class="btn primary" id="s_wa" target="_blank" rel="noopener">WhatsApp</a><a class="btn" id="s_sms">Sms</a><a class="btn" id="s_mail">Mail</a>
        <button class="btn" id="s_copy">Kopieer tekst</button><button class="btn" id="s_native" hidden>Deelmenu</button>`;
}
function deelKnoppenKoppelen(kaart, p, onderwerp) {
  const $ = (id) => p.querySelector("#" + id);
  const app = inHaApp(kaart._hass);
  const upd = () => {
    const l = deelLinks($("stext").value, onderwerp, app);
    for (const k of ["wa", "sms", "mail"]) {
      $("s_" + k).href = l[k];
      if (app || k === "wa") $("s_" + k).target = "_blank"; else $("s_" + k).removeAttribute("target");
      $("s_" + k).rel = "noopener";
    }
    $("s_native").dataset.url = l.menu || "";
  };
  $("stext").addEventListener("input", upd);
  upd();
  $("s_copy").addEventListener("click", async () => {
    const ok = await kopieer($("stext").value, $("stext"));
    const k = $("s_copy");
    k.textContent = ok ? "Gekopieerd" : "Niet gelukt";
    setTimeout(() => { k.textContent = "Kopieer tekst"; }, 2500);
    kaart._message(ok ? "Tekst gekopieerd." : "Kopiëren lukt hier niet. De tekst is geselecteerd: hou ze ingedrukt en kies Kopiëren, of gebruik Deelmenu.", ok);
  });
  if (navigator.share || app) {
    $("s_native").hidden = false;
    $("s_native").addEventListener("click", () => {
      if (navigator.share) return navigator.share({ text: $("stext").value }).catch(() => {});
      window.open($("s_native").dataset.url, "_blank", "noopener");
    });
  }
}

/* Foto uit het portaal: enkel beheerders, via een ondertekend pad. */
const FOTO_SRC = new Map();
async function fotoSrc(hass, id, v) {
  const key = `${id}:${v || 0}`;
  const hit = FOTO_SRC.get(key);
  if (hit && hit.until > Date.now()) return hit.path;
  const r = await hass.callWS({ type: "auth/sign_path", path: `/api/btechnics_toegang/foto/${id}?v=${v || 0}`, expires: 3600 });
  const path = r.path;
  FOTO_SRC.set(key, { path, until: Date.now() + 3000 * 1000 });
  return path;
}

class VtoBase extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this.shadowRoot.addEventListener("click", (e) => {
      const p = e.target.closest("[data-photo]");
      if (!p) return;
      e.stopPropagation();
      openPhoto(this.shadowRoot, this._hass, p.dataset.photo, p.dataset.cap);
    });
  }
  setConfig(config) {
    this._config = config || {};
  }
  set hass(hass) {
    this._hass = hass;
    // na een scriptwissel kan _fmt nog van de oude versie zijn (zonder de nieuwe notaties)
    if (!this._fmt || !this._fmt.longDate) this._fmt = formatters(hass.config && hass.config.time_zone);
    // de rol (beheerder of niet) bepaalt wat de kaart toont: wachten tot de gebruiker gekend is,
    // en opnieuw opbouwen als de rol verandert
    if (!hass.user) return;
    const adm = isAdmin(hass);
    if (this._role === undefined || this._role !== adm) {
      this._role = adm;
      clearInterval(this._timer);
      this._timer = null;
      this._init();
    } else this._hassChanged();
  }
  connectedCallback() {
    if (this._locL && !this._locOn) { window.addEventListener("location-changed", this._locL); this._locOn = true; }
    if (!this._escL) this._escL = (e) => { if (e.key === "Escape") this._escape(); };
    document.addEventListener("keydown", this._escL);
  }
  disconnectedCallback() {
    if (this._locOn) { window.removeEventListener("location-changed", this._locL); this._locOn = false; }
    if (this._escL) document.removeEventListener("keydown", this._escL);
  }
  // paginawissel naar dit dashboard: kijken of er een opdracht klaarstaat (bv. meteen een tijdelijke code)
  _listenLoc() {
    if (!this._locL) this._locL = () => setTimeout(() => this._checkIntent(), 400);
    if (this.isConnected && !this._locOn) { window.addEventListener("location-changed", this._locL); this._locOn = true; }
  }
  _checkIntent() {}
  _escape() {}
  get hass() {
    return this._hass;
  }
  _init() {}
  _hassChanged() {}
  _modeChanged() {}
  // gsm-weergave als de kaart smaller is dan 600 px (de breedte van de kaart, niet van het scherm)
  _watchMode() {
    const apply = () => {
      const w = this.clientWidth || window.innerWidth;
      const m = w > 0 && w < 600;
      const card = this.shadowRoot.querySelector("ha-card");
      if (card) card.classList.toggle("m", m);
      if (m !== this._mobile) { const was = this._mobile; this._mobile = m; if (was !== undefined) this._modeChanged(); }
    };
    this._mobile = undefined;
    apply();
    if (!this._mro) { this._mro = new ResizeObserver(() => apply()); this._mro.observe(this); }
    this._applyMode = apply;
  }
  getCardSize() {
    return 6;
  }
  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }
  _ws(msg) {
    return this._hass.callWS(msg);
  }
}

/* ------------------------------------------------------------------------ overzicht */

class VtoOverzicht extends VtoBase {
  _init() {
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}
      .doors { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
      .door { border: 1px solid var(--divider-color); border-radius: var(--ha-card-border-radius, 12px); padding: 16px; display: flex; flex-direction: column; gap: 12px; }
      .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
      .name { font-size: 1.15rem; font-weight: 500; }
      .chip { font-size: 0.8rem; padding: 2px 10px; border-radius: 12px; background: var(--secondary-background-color); color: var(--secondary-text-color); display: inline-flex; align-items: center; gap: 6px; }
      .chip.off { color: var(--error-color, ${C_REFUSED}); }
      .last { display: flex; gap: 12px; align-items: center; height: 88px; }
      .last .txt { flex: 1; min-width: 0; }
      .last .line { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.35; }
      .last .txt.full .line { white-space: normal; overflow: visible; }
      .last .txt { cursor: default; }
      .pic { width: 104px; height: 64px; flex: none; border-radius: 8px; background: var(--secondary-background-color);
        display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color); overflow: hidden; }
      .pic button.thumb, .pic button.thumb img { width: 104px; height: 64px; }
      .stat .l { min-height: 2.7em; line-height: 1.35; }
      table.recent { table-layout: fixed; width: 100%; }
      table.recent td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; height: 22px; }
      table.recent col.c1 { width: 5.6em; } table.recent col.c3 { width: 26px; } table.recent col.c4 { width: 26px; } table.recent col.c5 { width: 28px; }
      table.recent td.c4 ha-icon { --mdc-icon-size: 18px; }
      table.recent td.c4 .open { color: ${C_OPEN}; } table.recent td.c4 .refused { color: ${C_REFUSED}; }
      table.recent td.c3, table.recent td.c4, table.recent td.ph { text-align: center; padding-left: 0; padding-right: 0; color: var(--secondary-text-color); --mdc-icon-size: 17px; }
      table.recent tr[data-row] { cursor: pointer; }
      table.recent tr.full td { white-space: normal; overflow: visible; height: auto; word-break: break-word; }
      table.recent button.photo { padding: 0; min-height: 0; height: 22px; width: 24px; }
      @media (max-width: 480px) {
        .pic, .pic button.thumb, .pic button.thumb img { width: 72px; height: 48px; }
        .last { height: 80px; } .who.line { font-size: 1.05rem; }
      }
      table.recent button.photo ha-icon { --mdc-icon-size: 16px; }
      .icon { width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; }
      .icon.open { background: rgba(2, 136, 209, 0.15); color: ${C_OPEN}; }
      .icon.refused { background: rgba(219, 68, 55, 0.15); color: ${C_REFUSED}; }
      .icon.none { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .who { font-size: 1.2rem; font-weight: 500; }
      .who.line { font-size: 1.2rem; }
      .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
      .stat { background: var(--secondary-background-color); border-radius: 8px; padding: 8px; }
      .stat .v { font-size: 1.2rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .stat .l { font-size: 0.8rem; color: var(--secondary-text-color); }
      .recent td { padding: 6px 4px; font-size: 0.9rem; }
      .foot { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 12px; }
      .opener { display: flex; align-items: center; gap: 10px; flex-wrap: nowrap; min-height: 44px; }
      .opener .omsg { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
      .opener button.btn { display: inline-flex; align-items: center; gap: 6px; }
      .opener button.btn.sure { background: var(--error-color, ${C_REFUSED}); color: #fff; border-color: var(--error-color, ${C_REFUSED}); }
      .opener .omsg { font-size: 0.85rem; }
      .opener .omsg.ok { color: var(--success-color, #0b8043); }
      .opener .omsg.error { color: var(--error-color, ${C_REFUSED}); }
      button.tohist { display: none; }
      /* gsm: geen grote laatste-toegang (staat ook bovenaan de lijst), cijfers van vandaag op een rij, grote knoppen */
      ha-card.m .doors { gap: 12px; }
      ha-card.m .door { padding: 12px; gap: 10px; }
      ha-card.m .last { display: none; }
      ha-card.m .stats { grid-template-columns: 1fr 1fr; }
      ha-card.m .stats .stat:nth-child(n+3) { display: none; }
      ha-card.m .stat .l { min-height: 0; }
      ha-card.m table.recent td { height: 36px; vertical-align: middle; font-size: 0.95rem; }
      ha-card.m table.recent col.c5 { width: 44px; }
      ha-card.m table.recent td { height: 40px; }
      ha-card.m table.recent button.photo { width: 40px; height: 40px; border-radius: 8px; background: var(--secondary-background-color); }
      ha-card.m table.recent button.photo ha-icon { --mdc-icon-size: 18px; }
      ha-card.m .opener { order: 9; flex-wrap: wrap; }
      ha-card.m .opener button.btn { width: 100%; justify-content: center; min-height: 48px; font-size: 1rem; font-weight: 500;
        background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      ha-card.m .opener button.btn.sure { background: var(--error-color, ${C_REFUSED}); border-color: var(--error-color, ${C_REFUSED}); }
      ha-card.m .opener .omsg { white-space: normal; }
      /* gebruiker zonder beheerrechten: enkel status en aantallen van vandaag */
      ha-card.user .last, ha-card.user table.recent, ha-card.user .stats .stat:nth-child(n+3) { display: none; }
      ha-card.user .stats { grid-template-columns: 1fr 1fr; }
      ha-card.m button.tohist { display: block; order: 8; background: none; border: 0; color: var(--primary-color); font: inherit; padding: 6px 0; min-height: 44px; cursor: pointer; }
    </style><ha-card class="${isAdmin(this._hass) ? "" : "user"}"><div class="title">${esc(this._config.title || "Deuren")}</div><div id="body" class="muted">Laden...</div><div id="foot" class="foot"></div>
      ${FAB}<div class="fabspace monly"></div></ha-card>`;
    const fab = this.shadowRoot.getElementById("fab");
    if (fab) fab.addEventListener("click", () => goView(this._config.codes_view || "codes", { quick: 1 }));
    this._watchMode();
    this._load();
    if (this.isConnected) this._timer = setInterval(() => this._load(), 30000);
  }
  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._timer);
    this._timer = null;
  }
  connectedCallback() {
    super.connectedCallback();
    if (this._hass && this._role !== undefined && !this._timer) {
      this._load();
      this._timer = setInterval(() => this._load(), 30000);
    }
  }
  _hassChanged() {
    // opnieuw laden zodra een "laatste unlock" sensor verandert (nieuwe toegang of verbinding)
    if (!this._data) return;
    const sig = this._data.doors.map((d) => { const s = this._hass.states[d.entities.last_unlock]; return s ? s.last_updated : ""; }).join("|");
    if (sig !== this._sig) {
      this._sig = sig;
      this._load();
    }
  }
  async _load() {
    try {
      const data = await this._ws({ type: "btechnics_toegang/doors" });
      this._data = data;
      this._sig = this._data.doors.map((d) => { const s = this._hass.states[d.entities.last_unlock]; return s ? s.last_updated : ""; }).join("|");
      if (this._busyOpen || this.shadowRoot.querySelector("[data-sure='1']")) return;
      this._render();
    } catch (e) {
      this.shadowRoot.getElementById("body").innerHTML = `<div class="error">Kon de deuren niet laden: ${esc(errText(e))}</div>`;
    }
  }
  _render() {
    const f = this._fmt;
    const doors = this._data.doors;
    const body = this.shadowRoot.getElementById("body");
    body.className = "";
    if (!doors.length) {
      body.innerHTML = `<div class="empty">Nog geen deuren. Maak er een in het tabblad Deuren.</div>`;
      return;
    }
    body.innerHTML = `<div class="doors">${doors.map((d) => this._door(d, f)).join("")}</div>`;
    body.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => this._open(b)));
    body.querySelectorAll("[data-tohist]").forEach((b) => b.addEventListener("click", () => goView(this._config.history_view || "historiek", { door: b.dataset.tohist })));
    // aanraken of klikken toont de volledige tekst (tooltip werkt niet op gsm)
    body.querySelectorAll("tr[data-row], [data-txt]").forEach((el) => el.addEventListener("click", (e) => {
      if (e.target.closest("[data-photo]")) return;
      el.classList.toggle("full");
    }));
    body.querySelectorAll("img[data-thumb]").forEach(async (img) => {
      try { img.src = await photoSrc(this._hass, img.dataset.thumb); } catch (e) { const bt = img.closest("button"); bt.outerHTML = '<ha-icon icon="mdi:camera-off-outline"></ha-icon>'; }
    });
    const a = this._data.archive || {};
    const first = Object.values(a).map((x) => x.first).filter(Boolean).sort()[0];
    this.shadowRoot.getElementById("foot").textContent = !isAdmin(this._hass) ? "" : first
      ? `Archief sinds ${f.dateTime.format(new Date(first * 1000))}, ${Object.values(a).reduce((n, x) => n + x.count, 0).toLocaleString("nl-BE")} toegangen bewaard.`
      : "";
  }
  _door(d, f) {
    const u = d.last_unlock;
    const cls = !d.available ? "none" : !u ? "none" : u.opened ? "open" : "refused";
    const icon = !d.available ? "mdi:lan-disconnect" : !u ? "mdi:door" : u.opened ? "mdi:door-open" : "mdi:door-closed-lock";
    const who = !d.available && !u ? (d.tablets ? "Tablet offline" : "Geen tablet") : !u ? "Nog geen toegang" : u.name === "?" ? "Onbekende code" : u.name;
    // twee vaste regels: hoe, en wanneer
    const how = !u ? "" : String(u.method).startsWith("op afstand") ? `${u.opened ? "Geopend" : "Geweigerd"} op afstand`
      : `${u.opened ? "Geopend" : "Geweigerd"} via ${u.method}`;
    const line1 = !d.available ? (u ? `Laatst gekend: ${how}` : (d.tablets ? "Tablet offline" : "Nog geen tablet gekoppeld")) : how;
    const line2 = u ? f.noYear.format(new Date(u.ts * 1000)) : "";
    const full = u ? `${who}\n${line1}\n${f.dateTime.format(new Date(u.ts * 1000))}` : who;
    const admin = isAdmin(this._hass);
    const recent = (d.recent || []).slice(0, 6).map((r) => { const when = new Date(r.ts * 1000);
      const nm = r.name === "?" ? "Onbekende code" : r.name;
      const meth = r.method;
      return `<tr data-row title="${esc(`${nm}\n${f.dateTime.format(when)}\n${meth}\n${r.opened ? "Geopend" : "Geweigerd"}`)}">
        <td class="muted c1">${esc(f.compact.format(when))}</td>
        <td class="c2">${r.name === "?" || isLabel(r.name) ? `<span class="muted">${esc(nm)}</span>` : esc(nm)}</td>
        <td class="c3">${methodIcon(meth)}</td>
        <td class="c4"><ha-icon class="${r.opened ? "open" : "refused"}" icon="${r.opened ? "mdi:check-circle" : "mdi:close-circle"}" title="${r.opened ? "Geopend" : "Geweigerd"}" aria-label="${r.opened ? "Geopend" : "Geweigerd"}"></ha-icon></td>
        ${admin ? `<td class="ph">${photoBtn(this._hass, r, `${nm}, ${d.name}, ${f.dateTime.format(when)}`)}</td>` : ""}</tr>`; }).join("")
      + `<tr><td colspan="${admin ? 5 : 4}">&nbsp;</td></tr>`.repeat(Math.max(0, 6 - Math.min(6, (d.recent || []).length)));
    const thumb = `<div class="pic">${u && u.photo && isAdmin(this._hass)
      ? `<button class="thumb" data-photo="${esc(u.photo)}" data-cap="${esc(`${who}, ${d.name}, ${f.dateTime.format(new Date(u.ts * 1000))}`)}" title="Foto bekijken"><img data-thumb="${esc(u.photo)}" alt="Foto van de laatste toegang"></button>`
      : `<ha-icon icon="mdi:camera-off-outline" title="Geen foto"></ha-icon>`}</div>`;
    return `<div class="door">
      <div class="head"><span class="name">${esc(d.name)}</span>
        <span class="chip ${d.available ? "" : "off"}">${d.available ? "Online" : d.tablets ? "Offline" : "Geen tablet"}</span></div>
      <div class="last"><div class="icon ${cls}"><ha-icon icon="${icon}"></ha-icon></div>
        <div class="txt" data-txt title="${esc(full)}"><div class="who line">${esc(who)}</div><div class="muted line">${esc(line1)}</div><div class="muted line">${esc(line2) || "&nbsp;"}</div></div>${thumb}</div>
      <div class="stats">
        <div class="stat"><div class="v">${d.today.opened}</div><div class="l">Vandaag geopend</div></div>
        <div class="stat"><div class="v">${d.today.refused}</div><div class="l">Vandaag geweigerd</div></div>
        <div class="stat"><div class="v">${d.codes}</div><div class="l">Codes</div></div>
        <div class="stat"><div class="v">${d.cards}/${d.tablets}</div><div class="l">Tablets online</div></div>
      </div>
      ${isAdmin(this._hass) ? `<div class="opener"><button class="btn" data-open="${esc(d.id)}" data-name="${esc(d.name)}" ${d.can_open ? "" : "disabled title=\"Kies eerst in Deuren waarmee deze deur opent\""}>
        <ha-icon icon="mdi:door-open" style="--mdc-icon-size:18px"></ha-icon> Deur openen</button><span class="omsg" data-omsg="${esc(d.id)}"></span></div>` : ""}
      <button class="tohist" data-tohist="${esc(d.id)}">Alles van ${esc(d.name)} in Historiek &rsaquo;</button>
      <table class="recent"><colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4">${admin ? '<col class="c5">' : ""}</colgroup>${recent}</table>
    </div>`;
  }
  async _open(b) {
    // twee klikken: eerst bevestigen, zodat een deur nooit per ongeluk opengaat
    const msg = this.shadowRoot.querySelector(`[data-omsg="${CSS.escape(b.dataset.open)}"]`);
    if (b.dataset.sure !== "1") {
      b.dataset.sure = "1";
      b.classList.add("sure");
      b.innerHTML = `<ha-icon icon="mdi:alert" style="--mdc-icon-size:18px"></ha-icon> Zeker ${esc(b.dataset.name)} openen?`;
      msg.className = "omsg"; msg.textContent = "Klik nog eens om te openen.";
      clearTimeout(this._sureT);
      this._sureT = setTimeout(() => this._render(), 6000);
      return;
    }
    clearTimeout(this._sureT);
    b.disabled = true;
    msg.className = "omsg"; msg.textContent = "Bezig...";
    this._busyOpen = true;
    try {
      await this._hass.callService("btechnics_toegang", "deur_openen", { deur: b.dataset.open });
      msg.className = "omsg ok"; msg.textContent = `${b.dataset.name} is geopend.`;
    } catch (e) {
      msg.className = "omsg error"; msg.textContent = `Niet gelukt: ${errText(e)}`;
    } finally {
      this._busyOpen = false;
      b.disabled = false; b.dataset.sure = ""; b.classList.remove("sure");
      b.innerHTML = `<ha-icon icon="mdi:door-open" style="--mdc-icon-size:18px"></ha-icon> Deur openen`;
      setTimeout(() => { if (!this._busyOpen) this._render(); }, 8000);
    }
  }
  static getStubConfig() {
    return {};
  }
}

/* ------------------------------------------------------------------------ toegang */

const PERIODS = [
  ["1", "Vandaag"], ["7", "7 dagen"], ["30", "30 dagen"], ["90", "90 dagen"], ["365", "1 jaar"], ["custom", "Van … tot …"],
];
const PAGE = 50;

class VtoToegang extends VtoBase {
  disconnectedCallback() {
    super.disconnectedCallback();
    clearTimeout(this._deb);
    clearTimeout(this._retry);
    clearTimeout(this._doorsRetry);
  }
  _escape() {
    const fs = this.shadowRoot.getElementById("fs");
    if (fs) fs.classList.remove("on");
  }
  _init() {
    if (!isAdmin(this._hass)) return this._initUser();
    const c = this._config;
    this._state = {
      search: "", person: null, door: "", status: "all",
      period: String(c.period || "365"), from: "", to: "", view: "list", offset: 0, day: null,
    };
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}
      .filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
      .filters input[type=search] { flex: 1 1 220px; min-width: 180px; }
      .dates { display: none; gap: 8px; align-items: center; }
      .dates.on { display: inline-flex; }
      .personchip { display: none; align-items: center; gap: 6px; padding: 4px 6px 4px 12px; border-radius: 16px; background: var(--secondary-background-color); }
      .personchip.on { display: inline-flex; }
      .personchip button { border: none; background: none; cursor: pointer; color: var(--secondary-text-color); padding: 2px; display: flex; }
      .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-bottom: 12px; }
      .kpi { background: var(--secondary-background-color); border-radius: 8px; padding: 10px 12px; }
      .kpi .v { font-size: 1.4rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .kpi .l { font-size: 0.85rem; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px; }
      .chartwrap { position: relative; margin: 4px 0 16px; }
      .legend { display: flex; gap: 16px; font-size: 0.85rem; color: var(--secondary-text-color); margin-bottom: 4px; }
      .legend span { display: inline-flex; align-items: center; gap: 6px; }
      svg { display: block; overflow: visible; max-width: 100%; }
      svg text { fill: var(--secondary-text-color); font-size: 11px; }
      svg .grid { stroke: var(--divider-color); stroke-width: 1; }
      .tip { position: absolute; pointer-events: none; background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 10px; font-size: 0.85rem; box-shadow: 0 2px 8px rgba(0,0,0,.15);
        display: none; white-space: nowrap; z-index: 2; }
      .tabs { display: flex; gap: 8px; margin-bottom: 8px; }
      .more { display: flex; justify-content: center; padding: 12px 0 0; }
      .info { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 8px; }
      td.when { white-space: nowrap; font-variant-numeric: tabular-nums; }
      .fs { display: contents; }
      .fs .fl { font-size: 0.85rem; color: var(--secondary-text-color); margin: 6px 0 -4px; }
      /* gsm: filters achter een knop, actieve filters als chips, grafiek ingeklapt, lijst in plaats van tabel */
      ha-card.m .filters { flex-wrap: nowrap; }
      ha-card.m .filters input[type=search] { min-width: 0; flex: 1 1 auto; min-height: 44px; }
      ha-card.m .fs { display: none; }
      ha-card.m .fs.on { display: flex; flex-direction: column; gap: 10px; }
      ha-card.m .fs.on select, ha-card.m .fs.on input, ha-card.m .fs.on button.btn { width: 100%; }
      ha-card.m .fs.on .dates.on { display: flex; flex-direction: column; align-items: stretch; }
      ha-card.m .fs h3 { margin: 0; font-size: 1.05rem; font-weight: 500; }
      button.fbtn { position: relative; min-height: 44px; flex: none; }
      button.fbtn i { position: absolute; top: -6px; right: -6px; font-style: normal; font-size: 0.7rem; background: #ED6928; color: #fff; border-radius: 9px; padding: 0 6px; line-height: 18px; }
      .fchips { display: flex; gap: 6px; flex-wrap: wrap; margin: -2px 0 10px; }
      .fchips button { border: 0; border-radius: 20px; padding: 6px 14px; min-height: 40px; background: var(--primary-color); color: var(--text-primary-color, #fff); font: inherit; font-size: 0.85rem; cursor: pointer; }
      ha-card.m .personchip.on { min-height: 34px; }
      ha-card.m .filters .personchip { display: none; }
      ha-card.m .kpis { grid-template-columns: repeat(3, 1fr); gap: 6px; }
      ha-card.m .kpi { padding: 6px 8px; } ha-card.m .kpi .v { font-size: 1.1rem; } ha-card.m .kpi .l { font-size: 0.75rem; }
      ha-card.m .kpi:nth-child(4) { display: none; }
      ha-card.m #chart:not(.open) { display: none; }
      ha-card.m .tabs { background: var(--secondary-background-color); border-radius: 10px; padding: 3px; gap: 3px; }
      ha-card.m .tabs .btn { flex: 1; border: 0; background: none; min-height: 40px; padding: 4px; }
      ha-card.m .tabs .btn.active { background: var(--card-background-color, #fff); color: var(--primary-text-color); font-weight: 500; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15); }
    </style>
    <ha-card>
      <div class="title">${esc(c.title || "Toegangshistoriek")}</div>
      <div class="filters">
        <input id="q" type="search" list="names" placeholder="Zoek persoon of badgenummer" autocomplete="off">
        <datalist id="names"></datalist>
        <button id="fbtn" class="btn fbtn monly">Filters</button>
        <span id="dchip" class="personchip"><span id="dname"></span><button id="dclear" title="Dagfilter wissen"><ha-icon icon="mdi:close" style="--mdc-icon-size:18px"></ha-icon></button></span>
        <span id="pchip" class="personchip"><span id="pname"></span><button id="pclear" title="Persoonfilter wissen"><ha-icon icon="mdi:close" style="--mdc-icon-size:18px"></ha-icon></button></span>
        <div class="shade" id="fshade"></div>
        <div id="fs" class="fs sheet">
          <h3 class="monly">Filters</h3>
          <div class="fl monly">Deur</div>
          <select id="door"><option value="">Alle deuren</option></select>
          <div class="fl monly">Resultaat</div>
          <select id="status"><option value="all">Alle</option><option value="opened">Geopend</option><option value="refused">Geweigerd</option></select>
          <div class="fl monly">Periode</div>
          <select id="period">${PERIODS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
          <span id="dates" class="dates"><input id="from" type="date"> tot <input id="to" type="date"></span>
          <button id="csv" class="btn" title="Exporteer de gefilterde toegangen als CSV (Excel)"><ha-icon icon="mdi:download" style="--mdc-icon-size:18px"></ha-icon> CSV</button>
          <button id="fdone" class="btn monly" style="background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:var(--primary-color)">Toon resultaten</button>
        </div>
      </div>
      <div id="fchips" class="fchips monly"></div>
      <div id="kpis" class="kpis"></div>
      <button id="cfold" class="fold monly">Grafiek per maand <span>tonen</span></button>
      <div id="chart" class="chartwrap"></div>
      <div class="tabs">
        <button id="tlist" class="btn active">Toegangen</button>
        <button id="tpeople" class="btn">Per persoon</button>
        <button id="tdays" class="btn">Per dag</button>
      </div>
      <div id="out" class="scroll"><div class="muted">Laden...</div></div>
      <div id="more" class="more"></div>
      <div id="info" class="info"></div>
      ${isAdmin(this._hass) ? `${FAB}<div class="fabspace monly"></div>` : ""}
    </ha-card>`;
    const $ = (id) => this.shadowRoot.getElementById(id);
    const it = takeIntent(c.view || "historiek");
    if (it && it.door) this._state.door = it.door;
    if ($("fab")) $("fab").addEventListener("click", () => goView(c.codes_view || "codes", { quick: 1 }));
    const fsOpen = (on) => $("fs").classList.toggle("on", on);
    $("fbtn").addEventListener("click", () => fsOpen(true));
    $("fdone").addEventListener("click", () => fsOpen(false));
    $("fshade").addEventListener("click", () => fsOpen(false));
    $("cfold").addEventListener("click", () => {
      this._chartOpen = !this._chartOpen;
      $("chart").classList.toggle("open", this._chartOpen);
      $("cfold").querySelector("span").textContent = this._chartOpen ? "verbergen" : "tonen";
      if (this._chartOpen && this._res) requestAnimationFrame(() => this._chart());
    });
    $("fchips").addEventListener("click", (e) => {
      const b = e.target.closest("[data-fclear]");
      if (!b) return;
      const k = b.dataset.fclear;
      if (k === "door") { this._state.door = ""; $("door").value = ""; }
      if (k === "status") { this._state.status = "all"; $("status").value = "all"; }
      if (k === "period") { this._state.period = String(c.period || "365"); $("period").value = this._state.period; $("dates").classList.remove("on"); }
      if (k === "person") return this._setPerson(null);
      if (k === "day") return this._setDay(null);
      this._reload();
    });
    $("period").value = this._state.period;
    $("q").addEventListener("input", (e) => {
      // waarde meteen uitlezen: na het event wijst e.target (shadow DOM) naar de kaart zelf
      const v = e.target.value.trim();
      clearTimeout(this._deb);
      this._deb = setTimeout(() => { this._state.search = v; this._reload(); }, 300);
    });
    $("q").addEventListener("change", (e) => {
      // keuze uit de lijst met namen: meteen exact op die persoon filteren
      const v = e.target.value.trim();
      if (this._names && this._names.includes(v)) this._setPerson(v);
    });
    $("pclear").addEventListener("click", () => this._setPerson(null));
    $("door").addEventListener("change", (e) => { this._state.door = e.target.value; this._reload(); });
    $("status").addEventListener("change", (e) => { this._state.status = e.target.value; this._reload(); });
    $("period").addEventListener("change", (e) => {
      this._state.period = e.target.value;
      $("dates").classList.toggle("on", this._state.period === "custom");
      if (this._state.period !== "custom") this._reload();
    });
    $("from").addEventListener("change", (e) => { this._state.from = e.target.value; this._reload(); });
    $("to").addEventListener("change", (e) => { this._state.to = e.target.value; this._reload(); });
    $("tlist").addEventListener("click", () => this._setView("list"));
    $("tpeople").addEventListener("click", () => this._setView("people"));
    $("tdays").addEventListener("click", () => this._setView("days"));
    $("dclear").addEventListener("click", () => this._setDay(null));
    $("csv").addEventListener("click", () => this._csv());
    if (!this._wired) {
      // een keer koppelen (ook na een scriptwissel die _init opnieuw draait)
      this._wired = true;
      const go = (e) => {
        if (e.target.closest("[data-photo]")) return;
        const b = e.target.closest("[data-person]");
        if (b) this._setPerson(b.dataset.person);
        const dd = e.target.closest("[data-day]");
        if (dd) this._setDay(dd.dataset.day);
      };
      this.shadowRoot.addEventListener("click", go);
      this.shadowRoot.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches(".mrow[tabindex]")) { e.preventDefault(); go(e); } });
    }
    this._loadDoors();
    this._reload();
    if (this._ro) this._ro.disconnect();
    this._ro = new ResizeObserver(() => {
      const w = Math.round(this.shadowRoot.getElementById("chart").clientWidth);
      if (this._res && w && Math.abs(w - (this._chartW || 0)) > 4) this._chart();
    });
    this._ro.observe(this.shadowRoot.getElementById("chart"));
    this._watchMode();
    // Home Assistant houdt een bezochte pagina soms verbonden: ook bij elke paginawissel naar een opdracht kijken
    this._listenLoc();
  }
  _initUser() {
    // gebruiker zonder beheerrechten: enkel aantallen per dag en per deur, geen namen, uren of foto's
    const c = this._config;
    this._user = { door: "", period: "30" };
    this._state = null;
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}
      .filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
      ha-card.m .filters select { flex: 1 1 140px; min-height: 44px; }
      .kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }
      .kpi { background: var(--secondary-background-color); border-radius: 8px; padding: 10px 12px; }
      .kpi .v { font-size: 1.4rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .kpi .l { font-size: 0.85rem; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px; }
      ha-card.m .kpi { padding: 6px 8px; } ha-card.m .kpi .v { font-size: 1.1rem; }
      .info { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 10px; }
    </style><ha-card>
      <div class="title">${esc(c.title || "Toegangshistoriek")}</div>
      <div class="filters">
        <select id="door"><option value="">Alle deuren</option></select>
        <select id="period"><option value="7">7 dagen</option><option value="30">30 dagen</option><option value="90">90 dagen</option><option value="365">1 jaar</option></select>
      </div>
      <div id="kpis" class="kpis"></div>
      <div id="out" class="scroll"><div class="muted">Laden...</div></div>
      <div class="info">Aantal toegangen per dag. Namen, uren en foto's zijn enkel zichtbaar voor beheerders.</div>
      ${FAB}<div class="fabspace monly"></div>
    </ha-card>`;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("period").value = this._user.period;
    $("fab").addEventListener("click", () => goView(c.codes_view || "codes", { quick: 1 }));
    $("door").addEventListener("change", (e) => { this._user.door = e.target.value; this._loadUser(); });
    $("period").addEventListener("change", (e) => { this._user.period = e.target.value; this._loadUser(); });
    const it = takeIntent(c.view || "historiek");
    if (it && it.door) this._user.door = it.door;
    const doorsUser = () => this._ws({ type: "btechnics_toegang/doors" }).then((d) => {
      $("door").innerHTML = `<option value="">Alle deuren</option>` + d.doors.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
      $("door").value = this._user.door;
    }).catch(() => { clearTimeout(this._doorsRetry); this._doorsRetry = setTimeout(() => this.isConnected && doorsUser(), 10000); });
    doorsUser();
    this._watchMode();
    this._listenLoc();
    this._loadUser();
  }
  async _loadUser() {
    const u = this._user, seq = (this._seq = (this._seq || 0) + 1);
    const $ = (id) => this.shadowRoot.getElementById(id);
    try {
      const r = await this._ws({ type: "btechnics_toegang/user/days", days: parseInt(u.period, 10), ...(u.door ? { door_ids: [u.door] } : {}) });
      if (seq !== this._seq) return;
      this._ures = r;
      $("kpis").innerHTML = `<div class="kpi"><div class="v">${r.total.toLocaleString("nl-BE")}</div><div class="l">Toegangen</div></div>
        <div class="kpi"><div class="v">${r.opened.toLocaleString("nl-BE")}</div><div class="l"><span class="dot open"></span>Geopend</div></div>
        <div class="kpi"><div class="v">${r.refused.toLocaleString("nl-BE")}</div><div class="l"><span class="dot refused"></span>Geweigerd</div></div>`;
      this._renderUser();
    } catch (e) {
      if (seq === this._seq) $("out").innerHTML = `<div class="error">Kon de historiek niet laden: ${esc(errText(e))}</div>`;
    }
  }
  _renderUser() {
    const r = this._ures, f = this._fmt, out = this.shadowRoot.getElementById("out");
    if (!r) return;
    if (!r.days.length) { out.innerHTML = `<div class="empty">Geen toegangen in deze periode</div>`; return; }
    const day = (d) => esc(f.date.format(new Date(`${d.day}T12:00:00Z`)));
    const doors = (d) => d.doors.map((x) => `${esc(x.door)} ${x.count}`).join(", ");
    out.innerHTML = this._mobile
      ? `<div class="mlist">${r.days.map((d) => `<div class="mrow" style="cursor:default"><span class="mt"><b>${day(d)}</b>
          <span class="sub">${d.count} toegangen${d.refused ? `, <span class="refusedtxt">${d.refused} geweigerd</span>` : ""}</span><span class="sub">${doors(d)}</span></span></div>`).join("")}</div>`
      : `<table><thead><tr><th>Dag</th><th class="num">Toegangen</th><th class="num">Geopend</th><th class="num">Geweigerd</th><th>Per deur</th></tr></thead><tbody>
          ${r.days.map((d) => `<tr><td class="when">${day(d)}</td><td class="num">${d.count}</td><td class="num">${d.opened}</td>
            <td class="num">${d.refused ? `<span class="refusedtxt">${d.refused}</span>` : 0}</td><td class="muted">${doors(d)}</td></tr>`).join("")}</tbody></table>`;
  }
  connectedCallback() {
    super.connectedCallback();
    this._checkIntent();
  }
  _checkIntent() {
    // opnieuw getoond (bv. via "Alles van Cafe in Historiek" op het overzicht): deurfilter overnemen
    if (this._user && this.isConnected) {
      const iu = takeIntent(this._config.view || "historiek");
      if (iu && iu.door) { this._user.door = iu.door; const sd = this.shadowRoot.getElementById("door"); if (sd) sd.value = iu.door; this._loadUser(); }
      return;
    }
    if (!this._hass || !this._state || !this.isConnected) return;
    const it = takeIntent(this._config.view || "historiek");
    if (it && it.door) {
      this._state.door = it.door;
      const sel = this.shadowRoot.getElementById("door");
      if (sel) sel.value = it.door;
      this._reload();
    }
  }
  _modeChanged() {
    this._escape();   // filtervenster van de gsm niet laten openstaan
    if (this._user) return this._renderUser();
    if (this._res) this._renderOut();
  }
  _syncFilters() {
    // gsm: teller op de knop Filters en chips voor de actieve filters
    const $ = (id) => this.shadowRoot.getElementById(id);
    if (!$("fchips")) return;
    const s = this._state, def = String(this._config.period || "365");
    const chips = [];
    if (s.person !== null) chips.push(["person", s.person === "?" ? "onbekende code" : s.person]);
    if (s.day) chips.push(["day", this._fmt.date.format(new Date(`${s.day}T12:00:00Z`))]);
    if (s.door) { const o = $("door").querySelector(`option[value="${CSS.escape(s.door)}"]`); chips.push(["door", o ? o.textContent : "Deur"]); }
    if (s.status !== "all") chips.push(["status", s.status === "opened" ? "Geopend" : "Geweigerd"]);
    if (s.period !== def && !s.day) chips.push(["period", s.period === "custom" ? `${s.from || "…"} tot ${s.to || "…"}` : (PERIODS.find((x) => x[0] === s.period) || [0, s.period])[1]]);
    $("fchips").innerHTML = chips.map(([k, l]) => `<button data-fclear="${k}" title="Filter wissen">${esc(l)} &#x2715;</button>`).join("");
    const n = (s.door ? 1 : 0) + (s.status !== "all" ? 1 : 0) + (s.period !== def ? 1 : 0);
    $("fbtn").innerHTML = `Filters${n ? `<i>${n}</i>` : ""}`;
  }
  async _loadDoors() {
    try {
      const d = await this._ws({ type: "btechnics_toegang/doors" });
      clearTimeout(this._doorsRetry);
      const sel = this.shadowRoot.getElementById("door");
      sel.innerHTML = `<option value="">Alle deuren</option>` + d.doors.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
      sel.value = this._state.door;
      this._archive = d.archive || {};
      this._syncFilters();
    } catch (e) {
      // bv. tijdens het opstarten van Home Assistant: later opnieuw proberen
      clearTimeout(this._doorsRetry);
      this._doorsRetry = setTimeout(() => this.isConnected && this._loadDoors(), 10000);
    }
  }
  _setPerson(p) {
    clearTimeout(this._deb); // een nog lopende zoekopdracht mag de persoonkeuze niet overschrijven
    this._state.person = p;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("pchip").classList.toggle("on", !!p);
    $("pname").textContent = p ? `Persoon: ${p === "?" ? "onbekende code" : p}` : "";
    if (p) { $("q").value = ""; this._state.search = ""; this._state.view = "list"; this._syncTabs(); }
    this._reload();
  }
  _setDay(d) {
    // een dag uit Per dag: enkel de toegangen van die dag tonen (periodekeuze blijft bewaard)
    this._state.day = d;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("dchip").classList.toggle("on", !!d);
    $("dname").textContent = d ? `Dag: ${this._fmt.date.format(new Date(`${d}T12:00:00Z`))}` : "";
    $("period").disabled = !!d;
    if (d) { this._state.view = "list"; this._syncTabs(); }
    this._reload();
  }
  _setView(v) {
    this._state.view = v;
    this._syncTabs();
    this._renderOut();
  }
  _syncTabs() {
    this.shadowRoot.getElementById("tlist").classList.toggle("active", this._state.view === "list");
    this.shadowRoot.getElementById("tpeople").classList.toggle("active", this._state.view === "people");
    this.shadowRoot.getElementById("tdays").classList.toggle("active", this._state.view === "days");
  }
  _filters() {
    const s = this._state;
    const m = {};
    if (s.door) m.door_ids = [s.door];
    if (s.search) m.search = s.search;
    if (s.person !== null) m.person = s.person;
    if (s.status !== "all") m.status = s.status;
    if (s.day) {
      m.date_from = s.day;
      m.date_to = s.day;
    } else if (s.period === "custom") {
      if (s.from) m.date_from = s.from;
      if (s.to) m.date_to = s.to;
    } else {
      m.days = parseInt(s.period, 10);
    }
    return m;
  }
  async _reload() {
    this._state.offset = 0;
    this._syncFilters();
    const seq = (this._seq = (this._seq || 0) + 1);
    clearTimeout(this._retry);
    const mb = this.shadowRoot.getElementById("morebtn");
    if (mb) mb.disabled = true;
    try {
      const res = await this._ws({ type: "btechnics_toegang/history", ...this._filters(), limit: PAGE, offset: 0 });
      if (seq !== this._seq) return; // er kwam intussen een nieuwere zoekopdracht
      this._res = res;
      if (!this._state.search && this._state.person === null) {
        this._names = res.people.map((p) => p.name).filter((n) => n !== "?" && !isLabel(n));
        this.shadowRoot.getElementById("names").innerHTML = this._names.map((n) => `<option value="${esc(n)}">`).join("");
      }
      this._render();
    } catch (e) {
      if (seq !== this._seq) return;
      if (e && e.code === "unauthorized") {
        this.shadowRoot.getElementById("out").innerHTML = `<div class="error">Enkel beheerders kunnen de toegangshistoriek bekijken.</div>`;
        return;
      }
      const permanent = e && typeof e === "object" && e.code && !["not_ready", "unknown_error", "timeout"].includes(e.code);
      this.shadowRoot.getElementById("out").innerHTML = `<div class="error">Kon de historiek niet laden: ${esc(errText(e))}.${permanent ? "" : " Nieuwe poging binnen 10 seconden."}</div>`;
      if (!permanent) this._retry = setTimeout(() => this.isConnected && this._reload(), 10000);
    }
  }
  async _loadMore() {
    if (this._busy || !this._res) return;
    this._busy = true;
    const seq = this._seq, res0 = this._res;
    const btn = this.shadowRoot.getElementById("morebtn");
    if (btn) btn.disabled = true;
    try {
      // zelfde momentopname (max_id): geen dubbele of verschoven rijen als er intussen toegangen bijkomen
      const res = await this._ws({ type: "btechnics_toegang/history", ...this._filters(), limit: PAGE, offset: res0.rows.length, max_id: res0.max_id });
      if (seq !== this._seq || res0 !== this._res) return; // filters gewijzigd terwijl we laadden
      this._res.rows = this._res.rows.concat(res.rows);
      this._renderOut();
    } catch (e) {
      if (btn) btn.disabled = false;
      this.shadowRoot.getElementById("more").insertAdjacentHTML("beforeend", `<div class="error">Laden mislukt: ${esc(errText(e))}</div>`);
    } finally {
      this._busy = false;
    }
  }
  _render() {
    const r = this._res;
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("kpis").innerHTML = `
      <div class="kpi"><div class="v">${r.total.toLocaleString("nl-BE")}</div><div class="l">Toegangen</div></div>
      <div class="kpi"><div class="v">${r.opened.toLocaleString("nl-BE")}</div><div class="l"><span class="dot open"></span>Geopend</div></div>
      <div class="kpi"><div class="v">${r.refused.toLocaleString("nl-BE")}</div><div class="l"><span class="dot refused"></span>Geweigerd</div></div>
      <div class="kpi"><div class="v">${r.people.filter((p) => p.name !== "?" && !isLabel(p.name)).length.toLocaleString("nl-BE")}</div><div class="l">Personen</div></div>`;
    this._chart();
    this._renderOut();
    const a = this._archive || {};
    const first = Object.values(a).map((x) => x.first).filter(Boolean).sort()[0];
    $("info").textContent = first ? `Het archief bevat toegangen vanaf ${this._fmt.dateTime.format(new Date(first * 1000))}. Toegangen ouder dan 400 dagen worden gewist.` : "";
  }
  _chart() {
    const wrap = this.shadowRoot.getElementById("chart");
    const months = this._res.months;
    const days = this._state.period === "custom" ? 999 : parseInt(this._state.period, 10);
    if (days < 30 || !months.length) { wrap.innerHTML = ""; return; }
    // aaneensluitende maanden, ook zonder toegangen
    const all = [];
    let [y, m] = months[0].month.split("-").map(Number);
    const [ly, lm] = months[months.length - 1].month.split("-").map(Number);
    const by = Object.fromEntries(months.map((x) => [x.month, x]));
    while (y < ly || (y === ly && m <= lm)) {
      const k = `${y}-${String(m).padStart(2, "0")}`;
      all.push({ key: k, label: `${MONTHS[m - 1]} ${String(y).slice(2)}`, full: `${MONTHS[m - 1]} ${y}`, ...(by[k] || { count: 0, opened: 0 }) });
      m += 1; if (m > 12) { m = 1; y += 1; }
    }
    const W = Math.max(280, Math.round(wrap.clientWidth || 800)), H = 180, top = 18, bottom = 22, left = 36, right = 8;
    this._chartW = W;
    const plotH = H - top - bottom, plotW = W - left - right;
    const max = Math.max(1, ...all.map((x) => x.count));
    // kleinste "mooie" bovengrens boven het maximum (bv. 548 -> 600), zodat de balken de hoogte benutten
    const step = Math.pow(10, Math.floor(Math.log10(max)));
    const nice = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((f) => f * step).find((v) => v >= max) || max;
    const yv = (v) => top + plotH - (v / nice) * plotH;
    const band = plotW / all.length;
    const bw = Math.min(24, band * 0.6);
    const grid = (nice < 2 ? [0, nice] : [0, nice / 2, nice]).map((v) => `<line class="grid" x1="${left}" x2="${W - right}" y1="${yv(v)}" y2="${yv(v)}"></line>
      <text x="${left - 6}" y="${yv(v) + 4}" text-anchor="end">${Math.round(v).toLocaleString("nl-BE")}</text>`).join("");
    const bars = all.map((x, i) => {
      const cx = left + band * i + band / 2, x0 = cx - bw / 2;
      const ref = x.count - x.opened;
      const hO = (x.opened / nice) * plotH, hR = (ref / nice) * plotH;
      const base = top + plotH;
      const seg = (y1, h, color, rounded) => {
        if (h <= 0) return "";
        const r = rounded ? Math.min(4, h, bw / 2) : 0;
        const yt = y1 - h;
        return `<path fill="${color}" d="M${x0},${y1} V${yt + r} Q${x0},${yt} ${x0 + r},${yt} H${x0 + bw - r} Q${x0 + bw},${yt} ${x0 + bw},${yt + r} V${y1} Z"></path>`;
      };
      const gap = hO > 0 && hR > 0 ? 2 : 0;
      const label = all.length <= 14 && band >= 28 && x.count ? `<text x="${cx}" y="${base - hO - hR - gap - 5}" text-anchor="middle">${x.count}</text>` : "";
      const every = Math.max(1, Math.ceil(40 / band));
      const showX = i % every === 0;
      return `${seg(base, hO, C_OPEN, hR <= 0)}${seg(base - hO - gap, hR, C_REFUSED, true)}${label}
        ${showX ? `<text x="${cx}" y="${H - 6}" text-anchor="middle">${x.label}</text>` : ""}
        <rect class="hit" data-i="${i}" x="${left + band * i}" y="${top}" width="${band}" height="${plotH}" fill="transparent"></rect>`;
    }).join("");
    wrap.innerHTML = `<div class="legend"><span><span class="dot open"></span>Geopend</span><span><span class="dot refused"></span>Geweigerd</span><span>per maand</span></div>
      <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Toegangen per maand">${grid}${bars}</svg><div class="tip"></div>`;
    const tip = wrap.querySelector(".tip");
    wrap.querySelectorAll(".hit").forEach((el) => {
      el.addEventListener("mouseenter", () => {
        const x = all[+el.dataset.i];
        tip.innerHTML = `<b>${x.full}</b><br>${x.count} toegangen, ${x.opened} geopend, ${x.count - x.opened} geweigerd`;
        tip.style.display = "block";
      });
      el.addEventListener("mousemove", (e) => {
        const b = wrap.getBoundingClientRect();
        const px = e.clientX - b.left;
        tip.style.left = `${Math.min(px + 12, b.width - tip.offsetWidth)}px`;
        tip.style.top = `${e.clientY - b.top - 40}px`;
      });
      el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
    });
  }
  _renderOut() {
    const r = this._res;
    if (!r) return;
    const f = this._fmt;
    const out = this.shadowRoot.getElementById("out");
    const more = this.shadowRoot.getElementById("more");
    more.innerHTML = "";
    if (this._mobile) return this._renderMobile(r, f, out, more);
    if (this._state.view === "days") {
      const days = r.days || [];
      if (!days.length) { out.innerHTML = `<div class="empty">Geen toegangen gevonden</div>`; return; }
      out.innerHTML = `<table><thead><tr><th>Dag</th><th class="num">Toegangen</th><th class="num">Geopend</th><th class="num">Geweigerd</th><th class="num">Personen</th><th>Eerste</th><th>Laatste</th><th>Per deur</th></tr></thead><tbody>
        ${days.map((d) => `<tr>
          <td class="when"><button class="link" data-day="${esc(d.day)}" title="Toegangen van deze dag tonen">${esc(f.date.format(new Date(`${d.day}T12:00:00Z`)))}</button></td>
          <td class="num">${d.count}</td><td class="num">${d.opened}</td><td class="num">${d.refused ? `<span class="refusedtxt">${d.refused}</span>` : 0}</td><td class="num">${d.people}</td>
          <td class="when">${f.time.format(new Date(d.first * 1000))}</td><td class="when">${f.time.format(new Date(d.last * 1000))}</td>
          <td class="muted">${d.doors.map((x) => `${esc(x.door)} ${x.count}`).join(", ")}</td></tr>`).join("")}
        </tbody></table>`;
      return;
    }
    if (this._state.view === "people") {
      if (!r.people.length) { out.innerHTML = `<div class="empty">Geen toegangen gevonden</div>`; return; }
      out.innerHTML = `<table><thead><tr><th>Persoon</th><th class="num">Toegangen</th><th class="num">Geopend</th><th class="num">Geweigerd</th><th>Laatst</th><th>Deuren</th></tr></thead><tbody>
        ${r.people.map((p) => `<tr>
          <td><button class="link${isLabel(p.name) ? " muted" : ""}" data-person="${esc(p.name)}">${p.name === "?" ? "onbekende code" : esc(p.name)}</button></td>
          <td class="num">${p.count}</td><td class="num">${p.opened}</td><td class="num">${p.refused}</td>
          <td class="when">${f.dateTime.format(new Date(p.last * 1000))}</td><td class="muted">${p.doors.map(esc).join(", ")}</td></tr>`).join("")}
        </tbody></table>`;
      return;
    }
    if (!r.rows.length) { out.innerHTML = `<div class="empty">Geen toegangen gevonden</div>`; return; }
    const phHead = isAdmin(this._hass) ? '<th class="ph" title="Foto"><ha-icon icon="mdi:camera-outline"></ha-icon></th>' : "";
    out.innerHTML = `<table><thead><tr><th>Datum</th><th>Tijd</th><th>Deur</th><th>Persoon</th><th>Methode</th><th>Status</th>${phHead}</tr></thead><tbody>
      ${r.rows.map((x) => { const d = new Date(x.ts * 1000); return `<tr>
        <td class="when">${f.date.format(d)}</td><td class="when">${f.time.format(d)}</td><td>${esc(x.door)}</td>
        <td>${x.name === "?" ? '<button class="link muted" data-person="?">onbekende code</button>' : `<button class="link${isLabel(x.name) ? " muted" : ""}" data-person="${esc(x.name)}">${esc(x.name)}</button>`}</td>
        <td class="muted">${esc(x.method)}${x.card ? ` <span class="mono">${esc(x.card)}</span>` : ""}</td>
        <td><span class="status"><span class="dot ${x.opened ? "open" : "refused"}"></span>${x.opened ? "Geopend" : "Geweigerd"}</span></td>${photoCell(this._hass, x, `${x.name === "?" ? "Onbekende code" : x.name}, ${x.door}, ${f.dateTime.format(d)}`)}</tr>`; }).join("")}
      </tbody></table>`;
    if (r.rows.length < r.total) {
      more.innerHTML = `<button class="btn" id="morebtn">Meer tonen (${r.rows.length} van ${r.total.toLocaleString("nl-BE")})</button>`;
      more.querySelector("#morebtn").addEventListener("click", () => this._loadMore());
    }
  }
  _renderMobile(r, f, out, more) {
    // gsm: lijsten in plaats van brede tabellen; alles past in de breedte van het scherm
    const none = `<div class="empty">Geen toegangen gevonden</div>`;
    const chev = '<ha-icon icon="mdi:chevron-right"></ha-icon>';
    const v = this._state.view;
    if (v === "days") {
      const days = r.days || [];
      out.innerHTML = days.length ? `<div class="mlist">${days.map((d) => `<div class="mrow" role="button" tabindex="0" data-day="${esc(d.day)}">
        <span class="mt"><b>${esc(f.date.format(new Date(`${d.day}T12:00:00Z`)))}</b>
        <span class="sub">${d.count} toegangen${d.refused ? `, <span class="refusedtxt">${d.refused} geweigerd</span>` : ""}, ${esc(f.time.format(new Date(d.first * 1000)))} tot ${esc(f.time.format(new Date(d.last * 1000)))}</span>
        <span class="sub">${d.doors.map((x) => `${esc(x.door)} ${x.count}`).join(", ")}</span></span>${chev}</div>`).join("")}</div>` : none;
      return;
    }
    if (v === "people") {
      out.innerHTML = r.people.length ? `<div class="mlist">${r.people.map((p) => `<div class="mrow" role="button" tabindex="0" data-person="${esc(p.name)}">
        <span class="mt"><b class="${isLabel(p.name) || p.name === "?" ? "muted" : ""}">${p.name === "?" ? "Onbekende code" : esc(p.name)}</b>
        <span class="sub">${p.count} toegangen${p.refused ? `, <span class="refusedtxt">${p.refused} geweigerd</span>` : ""}, laatst ${esc(f.compact.format(new Date(p.last * 1000)))}</span>
        <span class="sub">${p.doors.map(esc).join(", ")}</span></span>${chev}</div>`).join("")}</div>` : none;
      return;
    }
    if (!r.rows.length) { out.innerHTML = none; return; }
    // gisteren in de tijdzone van Home Assistant (ook rond de wissel naar zomer- of wintertijd een dag van 23 of 25 uur)
    const today = f.date.format(new Date());
    let yest = today;
    for (let h = 1; yest === today && h <= 48; h++) yest = f.date.format(new Date(Date.now() - h * 3600000));
    let last = "", html = "";
    for (const x of r.rows) {
      const d = new Date(x.ts * 1000), day = f.date.format(d);
      if (day !== last) { html += `<div class="mday">${day === today ? "Vandaag" : day === yest ? `Gisteren, ${esc(day)}` : esc(day)}</div>`; last = day; }
      html += accessRow(this._hass, f, x, { door: true });
    }
    out.innerHTML = `<div class="mlist">${html}</div>`;
    if (r.rows.length < r.total) {
      more.innerHTML = `<button class="btn" id="morebtn">Meer tonen (${r.rows.length} van ${r.total.toLocaleString("nl-BE")})</button>`;
      more.querySelector("#morebtn").addEventListener("click", () => this._loadMore());
    }
  }
  async _csv() {
    const btn = this.shadowRoot.getElementById("csv");
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      await this._csvExport();
    } catch (e) {
      this.shadowRoot.getElementById("info").innerHTML = `<span class="error">CSV export mislukt: ${esc(errText(e))}</span>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }
  async _csvExport() {
    // in blokken ophalen (geen bovengrens), allemaal uit dezelfde momentopname en dezelfde filters
    const CHUNK = 20000;
    const filt = this._filters();
    let rows = [], maxId;
    for (;;) {
      const res = await this._ws({ type: "btechnics_toegang/history", ...filt, limit: CHUNK, offset: rows.length, ...(maxId ? { max_id: maxId } : {}) });
      maxId = res.max_id;
      rows = rows.concat(res.rows);
      if (res.rows.length < CHUNK || rows.length >= res.total) break;
    }
    const f = this._fmt;
    // tekst die Excel als formule zou uitvoeren (=, +, -, @) onschadelijk maken
    const q = (v) => { let t = String(v ?? ""); if (/^[=+\-@\t\r]/.test(t)) t = "'" + t; return `"${t.replace(/"/g, '""')}"`; };
    const lines = [["Datum", "Tijd", "Deur", "Persoon", "Methode", "Badge", "Status"].map(q).join(";")];
    for (const x of rows) {
      const d = new Date(x.ts * 1000);
      lines.push([f.csvDate.format(d), f.csvTime.format(d), x.door, x.name === "?" ? "onbekende code" : x.name, x.method, x.card, x.opened ? "Geopend" : "Geweigerd"].map(q).join(";"));
    }
    const blob = new Blob([String.fromCharCode(0xfeff) + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const dn = new Date(); const pad = (n) => String(n).padStart(2, "0");
    a.download = `toegangen-${dn.getFullYear()}-${pad(dn.getMonth() + 1)}-${pad(dn.getDate())}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  getCardSize() {
    return 12;
  }
  static getStubConfig() {
    return { period: 365 };
  }
}

/* ------------------------------------------------------------------------ codes */

class VtoCodes extends VtoBase {
  _init() {
    // gebruiker zonder beheerrechten: enkel eigen tijdelijke codes maken, delen en stoppen
    this._userMode = !isAdmin(this._hass);
    this._show = false;
    this._revealed = new Set(); // codes die met een klik zichtbaar gemaakt zijn
    this._q = "";
    this._status = "all";
    this._kind = "all";
    this._form = null;      // { type, entry }
    this._auditAll = false;
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}
      .bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; align-items: center; }
      .bar input[type=search] { flex: 1 1 220px; min-width: 180px; }
      .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
      .hidden { letter-spacing: 2px; color: var(--secondary-text-color); }
      button.reveal { background: none; border: 0; padding: 2px 4px; margin: -2px -4px; border-radius: 6px; cursor: pointer; font: inherit; color: inherit;
        display: inline-flex; align-items: center; gap: 6px; --mdc-icon-size: 16px; }
      button.reveal ha-icon { color: var(--secondary-text-color); opacity: 0.6; }
      button.reveal:hover { background: var(--secondary-background-color); }
      button.reveal:hover ha-icon { opacity: 1; }
      .chip { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; padding: 2px 8px; border-radius: 12px;
        background: var(--secondary-background-color); margin: 0 4px 4px 0; white-space: nowrap; }
      .chip.stored { color: var(--secondary-text-color); border: 1px dashed var(--divider-color); background: none; }
      .acts { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
      .acts button.btn { min-height: 32px; padding: 4px 10px; font-size: 0.85rem; }
      button.btn.danger { color: var(--error-color, ${C_REFUSED}); }
      button.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      .dot.blocked { background: var(--warning-color, #f4b400); }
      .dot.retired { background: var(--disabled-text-color, #9e9e9e); }
      .warn { color: var(--warning-color, #b06000); font-size: 0.85rem; }
      .panel { border: 1px solid var(--divider-color); border-radius: 8px; padding: 12px; margin-bottom: 12px; display: none; }
      .panel.on { display: block; }
      .panel h3 { margin: 0 0 8px; font-size: 1rem; font-weight: 500; }
      .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 10px; }
      .row label { display: inline-flex; align-items: center; gap: 6px; }
      .row input[type=text] { min-width: 220px; }
      .msg { margin: 0 0 12px; font-size: 0.9rem; }
      .msg.ok { color: var(--success-color, #0b8043); }
      .audit td { font-size: 0.85rem; }
      td.when { white-space: nowrap; font-variant-numeric: tabular-nums; }
      td .pname { font-weight: 500; }
      button.linkbtn { background: none; border: 0; padding: 0; font: inherit; color: var(--primary-text-color); cursor: pointer; text-align: left; }
      button.linkbtn:hover, button.linkbtn:focus-visible { color: var(--primary-color); text-decoration: underline; }
      .hist .kpis { display: flex; flex-wrap: wrap; gap: 16px; margin: 4px 0 10px; font-size: 0.9rem; }
      .hist .kpis b { font-size: 1.1rem; font-weight: 500; font-variant-numeric: tabular-nums; }
      .hist .head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
      .hist td { font-size: 0.9rem; }
      .more { display: flex; justify-content: center; padding: 12px 0 0; }
      .valid { font-size: 0.8rem; color: var(--secondary-text-color); margin-top: 2px; }
      .share textarea { width: 100%; box-sizing: border-box; min-height: 150px; font: inherit; padding: 8px; border-radius: 8px;
        border: 1px solid var(--divider-color); background: var(--card-background-color, #fff); color: var(--primary-text-color); }
      .share .sendbar { display: flex; flex-wrap: wrap; gap: 8px; }
      .share a.btn { text-decoration: none; display: inline-flex; align-items: center; gap: 6px; font: inherit; color: var(--primary-text-color);
        background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 12px;
        min-height: 40px; box-sizing: border-box; cursor: pointer; }
      .share a.btn:hover { border-color: var(--primary-color); }
      .share a.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      @media (max-width: 640px) {
        table.list thead { display: none; }
        table.list, table.list tbody, table.list tr, table.list td { display: block; width: auto; }
        table.list tr { border-bottom: 1px solid var(--divider-color); padding: 8px 0; }
        table.list td { border: 0; padding: 2px 0; }
        table.list td.kind, table.list td.kind + td { display: inline-block; margin-right: 8px; }
        .acts { justify-content: flex-start; margin-top: 6px; }
      }
      /* gsm: rij per code of badge zonder knoppen, acties in een menu van onderaf */
      ha-card.m .bar { flex-wrap: nowrap; }
      ha-card.m .bar input[type=search] { min-width: 0; flex: 1 1 auto; min-height: 44px; }
      ha-card.m .bar #plus { min-height: 44px; flex: none; }
      ha-card.m .tabs { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; margin: 0 -12px 8px; padding: 0 12px 2px; gap: 6px; }
      ha-card.m .tabs::-webkit-scrollbar { display: none; }
      ha-card.m .tabs .btn { white-space: nowrap; border-radius: 20px; min-height: 40px; padding: 4px 14px; flex: none; }
      ha-card.m .tabs .btn.active { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      .tabs .tsep { flex: none; width: 1px; background: var(--divider-color); margin: 4px 2px; }
      .mrow .st { font-size: 0.85rem; }
      .mrow .st.blocked { color: var(--warning-color, #b06000); } .mrow .st.retired { color: var(--secondary-text-color); }
      .bigcode { display: flex; flex-direction: column; align-items: center; justify-content: center; width: 100%; min-height: 64px; margin: 10px 0 6px;
        border: 0; border-radius: 12px; background: var(--secondary-background-color); color: var(--primary-text-color); font: inherit; cursor: pointer; }
      .bigcode .mono, .bigcode .hidden { font-size: 1.6rem; letter-spacing: 4px; }
      .bigcode small { color: var(--secondary-text-color); font-size: 0.8rem; }
      .smsg { margin: 0 0 8px; }
      ha-card.user #kind, ha-card.user #toggle, ha-card.user #new, ha-card.user #newbadge, ha-card.user #plus, ha-card.user #tabs,
      ha-card.user .audtitle, ha-card.user #audit { display: none !important; }
      .uinfo { font-size: 0.85rem; color: var(--secondary-text-color); margin: -4px 0 12px; }
      .av img { width: 100%; height: 100%; object-fit: cover; border-radius: 50%; display: block; }
      .av { overflow: hidden; }
      .pcell { display: flex; gap: 10px; align-items: flex-start; } .pcell .av { width: 34px; height: 34px; font-size: 0.9rem; }
      .days { display: inline-flex; gap: 4px; flex-wrap: wrap; }
      .days button { border: 1px solid var(--divider-color); background: none; border-radius: 16px; min-width: 38px; min-height: 34px; font: inherit; font-size: 0.85rem; cursor: pointer; color: var(--primary-text-color); }
      .days button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      .blok { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 6px 0; border-bottom: 1px dashed var(--divider-color); }
      .invites { margin-top: 16px; } .invites .inv { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
      .invites .inv .mt { flex: 1; min-width: 180px; }
    </style>
    <ha-card class="${this._userMode ? "user" : ""}">
      <div class="title">${esc(this._config.title || (this._userMode ? "Tijdelijke codes" : "Codes"))}</div>
      ${this._userMode ? `<div class="uinfo">Je ziet hier enkel de tijdelijke codes die jij maakte. Home Assistant kiest de code; maximaal 7 dagen geldig en maximaal 10 tegelijk actief.</div>` : ""}
      <div class="bar">
        <input id="q" type="search" placeholder="Zoek persoon of code" autocomplete="off">
        <select id="kind" class="donly" style="display:none"><option value="all">Codes en badges</option><option value="code">Codes</option><option value="badge">Badges</option></select>
        <button id="toggle" class="btn donly"><ha-icon icon="mdi:eye" style="--mdc-icon-size:18px"></ha-icon> Codes tonen</button>
        <button id="quick" class="btn primary donly"><ha-icon icon="mdi:timer-outline" style="--mdc-icon-size:18px"></ha-icon> Tijdelijke code</button>
        <button id="new" class="btn primary donly"><ha-icon icon="mdi:plus" style="--mdc-icon-size:18px"></ha-icon> Nieuwe code</button>
        <button id="newbadge" class="btn primary donly"><ha-icon icon="mdi:email-fast-outline" style="--mdc-icon-size:18px"></ha-icon> Uitnodigen</button>
        <button id="plus" class="btn monly" title="Nieuw"><ha-icon icon="mdi:plus" style="--mdc-icon-size:20px"></ha-icon> Nieuw</button>
      </div>
      <div class="tabs" id="tabs"></div>
      <div class="shade" id="shade"></div>
      <div id="panel" class="panel sheet"></div>
      <div id="hist" class="panel hist sheet"></div>
      <div id="msg" class="msg"></div>
      <div id="out" class="scroll"><div class="muted">Laden...</div></div>
      <div id="invites"></div>
      <div class="title donly audtitle" style="margin-top:20px;font-size:1.05rem">Wijzigingen</div>
      <div id="audit" class="scroll"></div>
      ${FAB}<div class="fabspace monly"></div>
    </ha-card>`;
    const $ = (id) => this.shadowRoot.getElementById(id);
    if (this._userMode) $("q").placeholder = "Zoek op naam";
    $("fab").addEventListener("click", () => this._openQuick());
    $("plus").addEventListener("click", () => this._newSheet());
    $("shade").addEventListener("click", () => { if (this._busy) return; this._closeForm(); this._closeHistory(); });
    $("q").addEventListener("input", (e) => { this._q = e.target.value.trim().toLowerCase(); this._render(); });
    $("kind").addEventListener("change", (e) => { this._kind = e.target.value; this._render(); });
    $("toggle").addEventListener("click", () => this._toggleShow());
    $("new").addEventListener("click", () => this._openForm("add", null));
    $("quick").addEventListener("click", () => this._openQuick());
    $("newbadge").addEventListener("click", () => this._openInvite());
    if (!this._wired) this.shadowRoot.addEventListener("click", (e) => {
      const $ = (id) => this.shadowRoot.getElementById(id);
      const h = e.target.closest("[data-hist]");
      if (h) { if (this._mobile) this._closeForm(); return this._history(h.dataset.hist); }
      const rv = e.target.closest("[data-reveal]");
      if (rv) {
        // klik op een code: enkel die code tonen of weer verbergen
        const id = rv.dataset.reveal;
        if (this._revealed.has(id)) this._revealed.delete(id); else this._revealed.add(id);
        if (this._form && this._form.type === "sheet") this._entrySheet(this._form.entry);
        return this._render();
      }
      const sh = e.target.closest("[data-sheet]");
      if (sh) { const en = this._data && this._data.entries.find((x) => x.id === sh.dataset.sheet); if (en) this._entrySheet(en); return; }
      const nw = e.target.closest("[data-new]");
      if (nw) {
        const k = nw.dataset.new;
        this._closeForm();
        if (k === "quick") return this._openQuick();
        if (k === "show") return this._toggleShow();
        if (k === "invite") return this._openInvite();
        return this._openForm(k, null);
      }
      const kd = e.target.closest("[data-kind]");
      if (kd) { this._kind = this._kind === kd.dataset.kind ? "all" : kd.dataset.kind; $("kind").value = this._kind; return this._render(); }
      if (e.target.closest("#afold")) { this._auditOpen = !this._auditOpen; return this._render(); }
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const entry = this._data && this._data.entries.find((x) => x.id === b.dataset.id);
      this._action(b.dataset.act, entry);
    });
    this._wired = true;
    this._watchMode();
    this._listenLoc();
    this._load();
    if (this.isConnected) this._timer = setInterval(() => this._load(), 30000);
  }
  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._timer);
    this._timer = null;
  }
  connectedCallback() {
    super.connectedCallback();
    if (this._hass && this._role !== undefined && !this._timer) {
      this._load();
      this._timer = setInterval(() => this._load(), 30000);
    }
  }
  _modeChanged() {
    // venster van de gsm (rij-acties, Nieuw) niet als paneel laten staan op een breed scherm
    if (this._form && ["sheet", "new"].includes(this._form.type) && !this._busy) this._closeForm();
    if (this._data) this._render();
  }
  _escape() {
    if (this._busy) return;
    if (this._form) this._closeForm();
    if (this._hist && this._hist.name) this._closeHistory();
  }
  _checkIntent() {
    if (!this.isConnected || !this._data || this._busy) return;
    const it = takeIntent(this._config.view || "codes");
    if (it && it.quick) { this._closeForm(); this._closeHistory(); this._openQuick(); }
  }
  _toggleShow() {
    this._show = !this._show;
    this._revealed.clear();
    this.shadowRoot.getElementById("toggle").innerHTML = `<ha-icon icon="${this._show ? "mdi:eye-off" : "mdi:eye"}" style="--mdc-icon-size:18px"></ha-icon> ${this._show ? "Codes verbergen" : "Codes tonen"}`;
    this._render();
  }
  _newSheet() {
    // gsm: een knop Nieuw in plaats van drie
    const p = this.shadowRoot.getElementById("panel");
    this._message("");
    this._form = { type: "new" };
    const it = (k, icon, t, sub) => `<button class="act" data-new="${k}"><ha-icon icon="${icon}"></ha-icon><span style="flex:1">${t}</span><span class="muted" style="font-size:0.85rem">${sub}</span></button>`;
    p.innerHTML = `<h3>Nieuw</h3>${it("quick", "mdi:timer-outline", "Tijdelijke code", "pakket, technicus, gast")}${it("add", "mdi:dialpad", "Vaste code", "medewerker, vrijwilliger")}
      ${it("invite", "mdi:email-fast-outline", "Uitnodiging", "medewerker vult zelf in")}<div class="actsep"></div>
      ${it("show", this._show ? "mdi:eye-off" : "mdi:eye", this._show ? "Codes verbergen" : "Alle codes tonen", "")}
      <button class="act" id="cancel"><ha-icon icon="mdi:close"></ha-icon>Sluiten</button>`;
    p.querySelector("#cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
  }
  _entrySheet(e) {
    // gsm: alle acties voor een code of badge in een menu van onderaf
    const p = this.shadowRoot.getElementById("panel");
    this._message("");
    this._form = { type: "sheet", entry: e };
    const doors = (e.doors.length ? e.doors : e.stored).map((d) => d.name);
    const shown = this._show || this._revealed.has(e.id);
    const secret = this._userMode ? `<div class="bigcode" style="cursor:default"><span class="hidden">&bull;&bull;&bull;&bull;&bull;&bull;</span><small>tik op Delen om de code te zien</small></div>`
      : e.kind === "badge" ? `<div class="bigcode" style="cursor:default"><span class="mono">${esc(e.secret)}</span><small>badgenummer</small></div>`
      : `<button class="bigcode" data-reveal="${esc(e.id)}">${shown ? `<span class="mono">${esc(e.secret)}</span><small>tik om te verbergen</small>` : `<span class="hidden">&bull;&bull;&bull;&bull;&bull;&bull;</span><small>tik om de code te tonen</small>`}</button>`;
    const a = (act, icon, label, cls) => `<button class="act ${cls || ""}" data-act="${act}" data-id="${esc(e.id)}"><ha-icon icon="${icon}"></ha-icon>${label}</button>`;
    const share = (e.kind === "code" ? a("share", "mdi:share-variant", "Delen") : "")
      + (!this._userMode && e.soort === "vast" && e.status !== "retired" ? a("link", "mdi:link-variant", "Portaallink") : "");
    let main, danger;
    if (this._userMode) {
      const live = e.status !== "retired";
      main = live ? share : "";
      danger = live ? a("retire", "mdi:stop-circle-outline", "Stoppen", "danger") : "";
    } else if (e.status === "active") { main = share + a("edit", "mdi:pencil", "Aanpassen") + a("block", "mdi:lock-clock", "Blokkeren"); danger = a("retire", "mdi:account-cancel", "Uit dienst zetten", "danger"); }
    else if (this._waiting(e)) { main = share + a("unblock", "mdi:play", "Nu al activeren", "primary") + a("edit", "mdi:pencil", "Aanpassen"); danger = a("retire", "mdi:account-cancel", "Uit dienst zetten", "danger"); }
    else if (e.status === "blocked") { main = a("unblock", "mdi:lock-open-variant", "Deblokkeren", "primary") + a("block", "mdi:lock-clock", "Einde blokkering aanpassen") + a("edit", "mdi:pencil", "Aanpassen"); danger = a("retire", "mdi:account-cancel", "Uit dienst zetten", "danger"); }
    else { main = a("restore", "mdi:restore", "Herstellen", "primary") + a("edit", "mdi:pencil", "Aanpassen"); danger = a("forget", "mdi:delete-forever", "Definitief verwijderen", "danger"); }
    p.innerHTML = `<div class="shead">${this._avatar(e)}
        <div class="mt"><b>${esc(e.name || "?")}</b><span class="muted">${e.kind === "badge" ? "Badge" : "Code"}${doors.length ? ` · ${doors.map(esc).join(", ")}` : ""}</span></div></div>
      <div class="status"><span class="dot ${e.status === "active" ? "open" : e.status}"></span>${esc(this._statusText(e))}</div>
      ${e.valid_until && e.status !== "retired" ? `<div class="valid">Geldig tot ${esc(this._fmt.dateTime.format(new Date(e.valid_until)))}</div>` : ""}
      ${e.max_uses && e.status !== "retired" ? `<div class="valid">${e.max_uses === 1 ? "Eenmalig" : `${e.uses || 0} van ${e.max_uses} keer gebruikt`}</div>` : ""}
      ${e.status !== "active" && e.doors.length ? `<div class="warn">Nog actief op ${e.doors.map((d) => esc(d.name)).join(", ")}</div>` : ""}
      ${secret}
      ${main}${this._userMode ? "" : `<button class="act" data-hist="${esc(e.name || "")}"><ha-icon icon="mdi:history"></ha-icon>Geschiedenis</button>`}
      <div class="actsep"></div>${danger}
      <button class="act" id="cancel"><ha-icon icon="mdi:close"></ha-icon>Sluiten</button>`;
    p.querySelector("#cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
    this._fotos();
  }
  async _load() {
    // niet verversen terwijl een formulier open is of een actie loopt: anders verdwijnt de invoer
    if (this._busy || this._form) return;
    try {
      this._data = await this._ws({ type: this._userMode ? "btechnics_toegang/user/codes" : "btechnics_toegang/manage/list" });
      this._render();
      // vanop een ander tabblad op de oranje knop gedrukt: meteen het formulier Tijdelijke code
      this._checkIntent();
    } catch (e) {
      const msg = e && e.code === "unauthorized" ? "Enkel beheerders kunnen de codes bekijken." : `Kon de codes niet laden: ${esc(errText(e))}`;
      this.shadowRoot.getElementById("out").innerHTML = `<div class="error">${msg}</div>`;
    }
  }
  async _history(name, more) {
    // toegangsgeschiedenis van een persoon (codes en badges), laatste jaar, uit het archief
    const el = this.shadowRoot.getElementById("hist");
    if (!name) return;
    const f = this._fmt;
    if (!more) {
      this._hist = { name, res: null, seq: (this._hist ? this._hist.seq : 0) + 1 };
      el.innerHTML = `<div class="head"><h3>Geschiedenis: ${esc(name)}</h3><button class="btn" id="hclose">Sluiten</button></div><div class="muted">Laden...</div>`;
      el.classList.add("on");
      el.querySelector("#hclose").addEventListener("click", () => this._closeHistory());
      if (!this._mobile) el.scrollIntoView({ block: "nearest" });
    }
    const h = this._hist, seq = h.seq;
    try {
      const msg = { type: "btechnics_toegang/history", person: name, days: 365, limit: 25, offset: more ? h.res.rows.length : 0 };
      if (more) msg.max_id = h.res.max_id;
      const res = await this._ws(msg);
      if (!this._hist || this._hist.seq !== seq) return; // intussen een andere persoon gekozen of gesloten
      if (more) { h.res.rows = h.res.rows.concat(res.rows); } else { h.res = res; }
      const r = h.res;
      const last = r.rows[0];
      el.innerHTML = `<div class="head"><h3>Geschiedenis: ${esc(name)}</h3><button class="btn" id="hclose">Sluiten</button></div>
        <div class="kpis"><span><b>${r.total.toLocaleString("nl-BE")}</b> toegangen (laatste jaar)</span><span><b>${r.opened.toLocaleString("nl-BE")}</b> geopend</span>
          <span><b>${r.refused.toLocaleString("nl-BE")}</b> geweigerd</span>${last ? `<span>Laatst: <b>${esc(f.dateTime.format(new Date(last.ts * 1000)))}</b></span>` : ""}</div>
        ${r.rows.length && this._mobile ? `<div class="mlist">${r.rows.map((x) => accessRow(this._hass, f, x, { door: true, date: true, person: false })).join("")}</div>`
          : r.rows.length ? `<div class="scroll"><table><thead><tr><th>Wanneer</th><th>Deur</th><th>Hoe</th><th>Status</th>${isAdmin(this._hass) ? '<th class="ph" title="Foto"><ha-icon icon="mdi:camera-outline"></ha-icon></th>' : ""}</tr></thead><tbody>
          ${r.rows.map((x) => `<tr><td class="when">${esc(f.dateTime.format(new Date(x.ts * 1000)))}</td><td>${esc(x.door)}</td><td class="muted">${esc(x.method)}</td>
            <td><span class="status"><span class="dot ${x.opened ? "open" : "refused"}"></span>${x.opened ? "Geopend" : "Geweigerd"}</span></td>${photoCell(this._hass, x, `${name}, ${x.door}, ${f.dateTime.format(new Date(x.ts * 1000))}`)}</tr>`).join("")}
          </tbody></table></div>` : `<div class="empty">Geen toegangen in het laatste jaar.</div>`}
        ${r.rows.length < r.total ? `<div class="more"><button class="btn" id="hmore">Meer tonen (${r.rows.length} van ${r.total.toLocaleString("nl-BE")})</button></div>` : ""}`;
      el.querySelector("#hclose").addEventListener("click", () => this._closeHistory());
      const hm = el.querySelector("#hmore");
      if (hm) hm.addEventListener("click", () => { hm.disabled = true; this._history(name, true); });
    } catch (e) {
      if (!this._hist || this._hist.seq !== seq) return;
      el.insertAdjacentHTML("beforeend", `<div class="error">Kon de geschiedenis niet laden: ${esc(errText(e))}</div>`);
    }
  }
  _closeHistory() {
    const el = this.shadowRoot.getElementById("hist");
    this._hist = { seq: (this._hist ? this._hist.seq : 0) + 1 };
    el.classList.remove("on");
    el.innerHTML = "";
  }
  _message(text, ok) {
    const el = this.shadowRoot.getElementById("msg");
    el.className = "msg " + (ok ? "ok" : "error");
    el.textContent = text || "";
    // gsm: het venster van onderaf bedekt de melding, dus ook bovenaan in het venster tonen
    const p = this.shadowRoot.getElementById("panel");
    if (this._mobile && p && p.classList.contains("on")) {
      let sm = p.querySelector(".smsg");
      if (!sm) { sm = document.createElement("div"); p.prepend(sm); }
      sm.className = "smsg msg " + (ok ? "ok" : "error");
      sm.textContent = text || "";
    }
  }
  _waiting(e) {
    return e.status === "blocked" && e.valid_from && e.until === e.valid_from;
  }
  _statusText(e) {
    if (e.status === "active") return "Actief";
    if (this._waiting(e)) return "Wacht op begin: " + this._fmt.dateTime.format(new Date(e.valid_from));
    if (e.status === "retired") return "Uit dienst";
    if (!e.until) return "Geblokkeerd tot deblokkeren";
    return "Geblokkeerd tot " + this._fmt.dateTime.format(new Date(e.until));
  }
  _render() {
    if (!this._data) return;
    const { entries, audit } = this._data;
    const $ = (id) => this.shadowRoot.getElementById(id);
    const count = (s) => entries.filter((e) => e.status === s).length;
    // tijdelijk: met een geldigheid of een maximum aantal keer, en nog niet uit dienst
    const temp = (e) => e.status !== "retired" && !!(e.valid_until || e.valid_from || e.max_uses);
    const tabs = [["all", "Alles", entries.length], ["active", "Actief", count("active")], ["temp", "Tijdelijk", entries.filter(temp).length],
      ["blocked", "Geblokkeerd", count("blocked")], ["retired", "Uit dienst", count("retired")]];
    $("tabs").innerHTML = tabs.map(([v, l, n]) => `<button class="btn ${this._status === v ? "active" : ""}" data-tab="${v}">${l} (${n})</button>`).join("")
     ;
    $("tabs").querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { this._status = b.dataset.tab; this._render(); }));
    const q = this._q;
    const list = entries.filter((e) => (this._status === "all" || (this._status === "temp" ? temp(e) : e.status === this._status))
      && (this._kind === "all" || e.kind === this._kind)
      && (!q || e.name.toLowerCase().includes(q) || (!this._userMode && String(e.secret).toLowerCase().includes(q))));
    const sec = (e) => this._userMode ? `<span class="hidden" title="Delen toont de code">&bull;&bull;&bull;&bull;&bull;&bull;</span>`
      : e.kind === "badge"
      ? `<span class="mono">${esc(e.secret)}</span>`
      : this._show ? `<span class="mono">${esc(e.secret)}</span>`
      : this._revealed.has(e.id)
        ? `<button class="reveal" data-reveal="${esc(e.id)}" title="Klik om te verbergen"><span class="mono">${esc(e.secret)}</span><ha-icon icon="mdi:eye-off-outline"></ha-icon></button>`
        : `<button class="reveal" data-reveal="${esc(e.id)}" title="Klik om de code te tonen"><span class="hidden">&bull;&bull;&bull;&bull;&bull;&bull;</span><ha-icon icon="mdi:eye-outline"></ha-icon></button>`;
    const acts = (e) => {
      const b = (act, label, cls) => `<button class="btn ${cls || ""}" data-act="${act}" data-id="${esc(e.id)}">${label}</button>`;
      const share = e.kind === "code" ? b("share", "Delen") : "";
      if (this._userMode) return e.status === "retired" ? "" : share + b("retire", "Stoppen", "danger");
      const link = e.soort === "vast" && e.status !== "retired" ? b("link", "Portaallink") : "";
      if (e.status === "active") return share + link + b("edit", "Aanpassen") + b("block", "Blokkeren") + b("retire", "Uit dienst", "danger");
      if (this._waiting(e)) return share + b("edit", "Aanpassen") + b("unblock", "Nu al activeren") + b("retire", "Uit dienst", "danger");
      if (e.status === "blocked") return b("unblock", "Deblokkeren", "primary") + b("block", "Einde aanpassen") + b("edit", "Aanpassen") + b("retire", "Uit dienst", "danger");
      return b("restore", "Herstellen", "primary") + b("edit", "Aanpassen") + b("forget", "Definitief verwijderen", "danger");
    };
    const dot = (e) => `<span class="dot ${e.status === "active" ? "open" : e.status}"></span>`;
    if (!list.length) {
      $("out").innerHTML = `<div class="empty">${this._userMode && !q ? "Nog geen tijdelijke codes. Maak er een met de knop Tijdelijke code." : "Niets gevonden"}</div>`;
    } else if (this._mobile) {
      const f = this._fmt;
      $("out").innerHTML = `<div class="mlist">${list.map((e) => {
        const doors = (e.doors.length ? e.doors : e.stored).map((d) => d.name);
        const extra = [];
        const kort = (iso) => f.compact.format(new Date(iso));
        const st = e.status === "retired" ? "uit dienst" : this._waiting(e) ? `wacht tot ${kort(e.valid_from)}` : e.until ? `geblokkeerd tot ${kort(e.until)}` : "geblokkeerd";
        if (e.status !== "active") extra.push(`<span class="st ${e.status === "retired" ? "retired" : "blocked"}">${esc(st)}</span>`);
        else if (e.valid_until) extra.push(`<span class="st">tot ${esc(f.compact.format(new Date(e.valid_until)))}</span>`);
        if (e.max_uses === 1 && e.status !== "retired") extra.push('<span class="st">eenmalig</span>');
        if (e.owner_name) extra.push(`<span class="st">door ${esc(e.owner_name)}</span>`);
        return `<button class="mrow" data-sheet="${esc(e.id)}">${this._avatar(e)}
          <span class="mt"><b>${esc(e.name || "?")}</b><span class="sub">${e.soort === "tijdelijk" ? "tijdelijke code" : "code"}${doors.length ? ` · ${doors.map(esc).join(", ")}` : ""}</span>${extra.length ? `<span class="sub">${extra.join(" · ")}</span>` : ""}</span>
          <span class="dot ${e.status === "active" ? "open" : e.status}"></span><ha-icon icon="mdi:chevron-right"></ha-icon></button>`; }).join("")}</div>`;
    } else {
      $("out").innerHTML = `<table class="list"><thead><tr><th>Persoon</th><th>Soort</th><th>Code</th><th>Deuren</th><th>Status</th><th></th></tr></thead><tbody>
        ${list.map((e) => `<tr>
          <td><div class="pcell">${this._userMode ? "" : this._avatar(e)}<div>${this._userMode ? `<span class="pname">${esc(e.name || "?")}</span>` : `<button class="pname linkbtn" data-hist="${esc(e.name || "")}" title="Toegangsgeschiedenis van ${esc(e.name || "?")}">${esc(e.name || "?")}</button>`}
            ${[e.telefoon, e.email].filter(Boolean).length ? `<div class="valid">${[e.telefoon, e.email].filter(Boolean).map(esc).join(" · ")}</div>` : ""}
            ${e.schema && e.schema.length ? `<div class="valid">${esc(schemaTekst(e.schema))}</div>` : ""}</div></div></td>
          <td class="muted kind">${e.soort === "tijdelijk" ? "tijdelijk" : "vast"}</td>
          <td>${sec(e)}</td>
          <td>${e.doors.map((d) => `<span class="chip">${esc(d.name)}</span>`).join("")}${e.stored.map((d) => `<span class="chip stored" title="werkt nu niet (geblokkeerd, wachtend of uit dienst)">${esc(d.name)}</span>`).join("")}</td>
          <td><span class="status">${dot(e)}${esc(this._statusText(e))}</span>
            ${e.valid_until && e.status !== "retired" ? `<div class="valid">Geldig tot ${esc(this._fmt.dateTime.format(new Date(e.valid_until)))}</div>` : ""}
            ${e.max_uses && e.status !== "retired" ? `<div class="valid">${e.max_uses === 1 ? "Eenmalig" : `${e.uses || 0} van ${e.max_uses} keer gebruikt`}</div>` : ""}
            ${e.owner_name ? `<div class="valid">Gemaakt door ${esc(e.owner_name)}</div>` : ""}
            ${e.status !== "active" && e.doors.length ? `<div class="warn">Nog actief op ${e.doors.map((d) => esc(d.name)).join(", ")}</div>` : ""}</td>
          <td><div class="acts">${acts(e)}</div></td></tr>`).join("")}
        </tbody></table>`;
    }
    this._fotos();
    if (this._userMode) { $("audit").innerHTML = ""; $("invites").innerHTML = ""; return; }
    const inv = this._data.invites || [];
    $("invites").innerHTML = inv.length ? `<div class="invites"><div class="title" style="font-size:1.05rem;margin-bottom:4px">Openstaande uitnodigingen</div>
      ${inv.map((u) => `<div class="inv"><ha-icon icon="mdi:email-fast-outline" style="color:var(--secondary-text-color)"></ha-icon>
        <span class="mt"><b>${esc(u.naam || "Nieuwe medewerker")}</b><div class="valid">${esc(u.deuren.join(", "))} · link geldig tot ${esc(this._fmt.dateTime.format(new Date(u.verloopt)))}${u.door ? ` · door ${esc(u.door)}` : ""}</div></span>
        <button class="btn danger" data-invstop="${esc(u.id)}">Intrekken</button></div>`).join("")}</div>` : "";
    $("invites").querySelectorAll("[data-invstop]").forEach((b) => b.addEventListener("click", () =>
      this._run([{ _ws: { type: "btechnics_toegang/uitnodiging", actie: "intrekken", invite: b.dataset.invstop } }], "Uitnodiging ingetrokken")));
    const shown = this._auditAll ? audit : audit.slice(0, 15);
    if (this._mobile) {
      // gsm: Wijzigingen ingeklapt, als lijst
      $("audit").innerHTML = `<button class="fold" id="afold">Wijzigingen <span>${this._auditOpen ? "verbergen" : "tonen"}</span></button>`
        + (!this._auditOpen ? "" : audit.length ? `<div class="mlist">${shown.map((a) => `<div class="mrow" style="cursor:default"><span class="mt">
          <b>${esc(a.action)}: ${esc(a.name)} <span class="muted">(${a.kind === "badge" ? "badge" : a.kind === "deur" ? "deur" : "code"})</span></b>
          <span class="sub">${esc(this._fmt.compact.format(new Date(a.ts)))} · ${esc(a.user)}${(a.doors || []).length ? ` · ${esc(a.doors.join(", "))}` : ""}</span>
          ${a.detail ? `<span class="sub">${esc(a.detail)}</span>` : ""}</span></div>`).join("")}</div>
          ${audit.length > 15 ? `<div class="more"><button class="btn" id="auditmore">${this._auditAll ? "Minder tonen" : `Alles tonen (${audit.length})`}</button></div>` : ""}`
          : `<div class="muted">Nog geen wijzigingen.</div>`);
      const amm = $("auditmore");
      if (amm) amm.addEventListener("click", () => { this._auditAll = !this._auditAll; this._render(); });
      return;
    }
    $("audit").innerHTML = audit.length ? `<table class="audit"><thead><tr><th>Wanneer</th><th>Wie</th><th>Actie</th><th>Persoon</th><th>Deuren</th><th>Details</th></tr></thead><tbody>
      ${shown.map((a) => `<tr><td class="when">${this._fmt.dateTime.format(new Date(a.ts))}</td><td>${esc(a.user)}</td><td>${esc(a.action)}</td>
        <td>${esc(a.name)} <span class="muted">(${a.kind === "badge" ? "badge" : a.kind === "deur" ? "deur" : "code"})</span></td><td class="muted">${esc((a.doors || []).join(", "))}</td>
        <td class="muted">${esc(a.detail || "")}</td></tr>`).join("")}</tbody></table>
      ${audit.length > 15 ? `<div class="more"><button class="btn" id="auditmore">${this._auditAll ? "Minder tonen" : `Alles tonen (${audit.length})`}</button></div>` : ""}`
      : `<div class="muted">Nog geen wijzigingen.</div>`;
    const am = $("auditmore");
    if (am) am.addEventListener("click", () => { this._auditAll = !this._auditAll; this._render(); });
  }
  _action(act, e) {
    if (act === "edit" || act === "block") return this._openForm(act, e);
    if (act === "share") return this._openShare(e);
    if (act === "link") return this._portaalLink(e);
    if (act === "retire" && this._userMode) return this._openForm("confirm", e, "retire", `'${esc(e.name)}' stoppen? De code werkt meteen niet meer.`);
    if (act === "retire") return this._openForm("confirm", e, "retire",
      `'${esc(e.name)}' uit dienst halen? De ${e.kind === "badge" ? "badge" : "code"} wordt uitgeschakeld en bewaard, zodat je ze later kan herstellen.`);
    if (act === "forget") return this._openForm("confirm", e, "forget",
      `'${esc(e.name)}' definitief uit de lijst verwijderen? Dit kan niet ongedaan gemaakt worden. De toegangshistoriek blijft bewaard.`);
    return this._run({ action: act, entry: e.id }, act === "unblock" ? (this._waiting(e) ? "Geactiveerd" : "Gedeblokkeerd") : "Hersteld");
  }
  _localInput(ms) {
    // datum en uur voor een datetime-local veld, in de tijdzone van Home Assistant (niet die van de browser)
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: (this._hass.config || {}).time_zone || undefined,
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date(ms)).filter((p) => p.type !== "literal").map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour === "24" ? "00" : parts.hour}:${parts.minute}`;
  }
  _validityRows(e) {
    const vf = e && e.valid_from ? this._localInput(Date.parse(e.valid_from)) : "";
    const vu = e && e.valid_until ? this._localInput(Date.parse(e.valid_until)) : "";
    return `<div class="row"><label>Geldig vanaf <input id="vfrom" type="datetime-local" value="${vf}"></label>
        <label>Geldig tot <input id="vuntil" type="datetime-local" value="${vu}"></label>
        <span class="muted">Leeg = vanaf nu, zonder einde. Na het einde gaat de code vanzelf uit dienst (herstelbaar).</span></div>`;
  }
  _readValidity(p, curVu) {
    const full = (v) => (v && v.length === 16 ? v + ":00" : v || "");
    const vf = full(p.querySelector("#vfrom").value), vu = full(p.querySelector("#vuntil").value);
    if (vf && vu && vu <= vf) return { error: "Het einde van de geldigheid moet na het begin liggen." };
    if (vu && vu !== curVu && vu <= this._localInput(Date.now()) + ":00") return { error: "Het einde van de geldigheid ligt in het verleden." };
    return { vf, vu };
  }
  _shareText(e) {
    const f = this._fmt;
    const doors = (e.doors.length ? e.doors : e.stored).map((d) => d.name);
    const doorTxt = doors.length > 1 ? `${doors.slice(0, -1).join(", ")} en ${doors[doors.length - 1]}` : doors.join("");
    // deeltekst in lopende taal: "maandag 28 september 2026, 23u59" (geen "om", dag voluit en klein)
    const day = (iso) => f.longDate.format(new Date(iso));
    const hour = (iso) => f.time.format(new Date(iso));
    const when = (iso) => `${day(iso)}, ${hour(iso)}`;
    let valid = "Geldig vanaf nu, zonder einddatum.";
    if (e.valid_from && e.valid_until) valid = day(e.valid_from) === day(e.valid_until)
      ? `Geldig op ${day(e.valid_from)}, van ${hour(e.valid_from)} tot ${hour(e.valid_until)}.`
      : `Geldig van ${when(e.valid_from)} tot ${when(e.valid_until)}.`;
    else if (e.valid_from) valid = `Geldig vanaf ${when(e.valid_from)}.`;
    else if (e.valid_until) valid = `Geldig tot ${when(e.valid_until)}.`;
    if (e.max_uses) valid += e.max_uses === 1 ? " De code werkt maar een keer." : ` De code werkt maximaal ${e.max_uses} keer.`;
    const hi = /^(Pakket|Technicus|Gast|Andere)\b/.test(e.name) ? "Hallo" : `Hallo ${e.name}`;
    return `${hi},\n\nJe toegangscode voor Btechnics is ${e.secret}.\n${doors.length > 1 ? "Deuren" : "Deur"}: ${doorTxt}.\n${valid}\n\nZo open je de deur: typ je code op het scherm naast de deur. Hou de code voor jezelf.`;
  }
  _openQuick() {
    // Tijdelijke code in een paar klikken, naar het voorbeeld van eenmalige en tijdelijke pincodes bij slimme sloten:
    // doel kiezen, deur(en), geldigheid; Home Assistant kiest zelf een willekeurige vrije code; meteen delen.
    if (!this._data) return this._message("De codes zijn nog niet geladen.");
    const p = this.shadowRoot.getElementById("panel");
    const doors = this._data.doors;
    this._message("");
    this._form = { type: "quick" };
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem("btxtoeg_quick_doors") || "[]"); } catch (err) { saved = []; }
    const TYPES = [["Pakket", "24"], ["Technicus", "today"], ["Gast", "3d"], ["Andere", "24"]];
    const DUR = [["1", "1 uur"], ["today", "Vandaag"], ["24", "24 uur"], ["3d", "3 dagen"], ["7d", "1 week"], ["custom", "Van … tot …"]];
    const st = { type: "Pakket", dur: "24" };   // geen optie Eenmalig meer (keuze Matthias 03/10)
    const day = () => { const x = this._localInput(Date.now()); return `${Number(x.slice(8, 10))}/${Number(x.slice(5, 7))}`; };
    p.innerHTML = `<h3>Tijdelijke code</h3>
      <div class="row" id="qtype">${TYPES.map(([t]) => `<button class="btn" data-qt="${t}">${t}</button>`).join("")}</div>
      <div class="row"><label>Naam <input id="qname" type="text" maxlength="30"></label>
        <span class="muted">Bv. "Pakket bol" of "Technicus Fluvius". Komt in de historiek.</span></div>
      <div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" class="qd" value="${esc(d.id)}" ${saved.includes(d.id) || (!saved.length && d === doors[0]) ? "checked" : ""}> ${esc(d.name)}</label>`).join("")}</div>
      <div class="row" id="qdur">Geldig: ${DUR.map(([k, l]) => `<button class="btn" data-qd="${k}">${l}</button>`).join("")}</div>
      <div class="row" id="qcustom" style="display:none"><label>Van <input id="vfrom" type="datetime-local"></label><label>Tot <input id="vuntil" type="datetime-local"></label><span class="muted">Belgische tijd (tijdzone van Home Assistant). Laat Van leeg om meteen te starten.</span></div>
      <div class="row muted" id="qsum"></div>
      <div class="row"><button class="btn primary" id="ok">Maak code en deel</button><button class="btn" id="cancel">Annuleren</button></div>`;
    const $ = (id) => p.querySelector("#" + id);
    let nameTouched = false;
    $("qname").addEventListener("input", () => { nameTouched = true; });
    const until = () => {
      const now = Date.now(), H = 3600000;
      const endOfDay = () => { const x = this._localInput(now); return `${x.slice(0, 10)}T23:59:00`; };
      if (st.dur === "1") return this._localInput(now + H) + ":00";
      if (st.dur === "today") return endOfDay();
      if (st.dur === "24") return this._localInput(now + 24 * H) + ":00";
      if (st.dur === "3d") return this._localInput(now + 72 * H) + ":00";
      if (st.dur === "7d") return this._localInput(now + 168 * H) + ":00";
      const v = $("vuntil").value;
      return v ? (v.length === 16 ? v + ":00" : v) : "";
    };
    const draw = () => {
      p.querySelectorAll("[data-qt]").forEach((b) => b.classList.toggle("active", b.dataset.qt === st.type));
      p.querySelectorAll("[data-qd]").forEach((b) => b.classList.toggle("active", b.dataset.qd === st.dur));
      $("qcustom").style.display = st.dur === "custom" ? "flex" : "none";
      if (!nameTouched) $("qname").value = `${st.type} ${day()}`;
      const u = until();
      const f = st.dur === "custom" && $("vfrom").value ? ($("vfrom").value.length === 16 ? $("vfrom").value + ":00" : $("vfrom").value) : "";
      const toon = (x) => this._fmt.dateTime.format(new Date(this._localToMs(x)));
      $("qsum").textContent = u ? `Geldig ${f ? `van ${toon(f)} ` : ""}tot ${toon(u)}. Home Assistant kiest een willekeurige code van 6 cijfers.` : "Kies tot wanneer de code geldig is.";
    };
    p.querySelectorAll("[data-qt]").forEach((b) => b.addEventListener("click", () => {
      const t = TYPES.find((x) => x[0] === b.dataset.qt); st.type = t[0]; st.dur = t[1]; draw();
    }));
    p.querySelectorAll("[data-qd]").forEach((b) => b.addEventListener("click", () => {
      st.dur = b.dataset.qd;
      // Van … tot …: velden voorinvullen (nu tot morgen dezelfde tijd), zodat je enkel aanpast wat moet
      if (st.dur === "custom" && !$("vuntil").value) {
        const nu = Math.ceil(Date.now() / 900000) * 900000;
        $("vfrom").value = this._localInput(nu);
        $("vuntil").value = this._localInput(nu + 24 * 3600000);
      }
      draw();
    }));
    ["vfrom", "vuntil"].forEach((id) => $(id).addEventListener("input", draw));
    $("ok").addEventListener("click", () => {
      const name = $("qname").value.trim();
      const sel = [...p.querySelectorAll(".qd:checked")].map((x) => x.value);
      if (!name) return this._message("Geef een naam.");
      if (!sel.length) return this._message("Kies minstens een deur.");
      const vu = until();
      const vf = st.dur === "custom" && $("vfrom").value ? ($("vfrom").value.length === 16 ? $("vfrom").value + ":00" : $("vfrom").value) : "";
      if (!vu) return this._message("Kies tot wanneer de code geldig is.");
      if (vu <= this._localInput(Date.now()) + ":00") return this._message("Het einde ligt in het verleden.");
      if (vf && vu <= vf) return this._message("Het einde moet na het begin liggen.");
      if (this._userMode && this._localToMs(vu) - Math.max(Date.now(), vf ? this._localToMs(vf) : 0) > 7 * 86400000 + 60000)
        return this._message("Een tijdelijke code is maximaal 7 dagen geldig.");
      try { localStorage.setItem("btxtoeg_quick_doors", JSON.stringify(sel)); } catch (err) { /* niet erg */ }
      const msg = { action: "add", name, doors: sel, valid_until: vu };
      if (vf) msg.valid_from = vf;
      this._run(msg, "Tijdelijke code gemaakt", (res) => {
        const id = res && res.result && res.result.id;
        const ne = id && this._data.entries.find((x) => x.id === id);
        if (ne) this._openShare(ne, "Tijdelijke code gemaakt. Deel ze meteen:");
      });
    });
    $("cancel").addEventListener("click", () => this._closeForm());
    draw();
    p.classList.add("on");
    if (!this._mobile) p.scrollIntoView({ block: "nearest" });
  }
  _tzOffset(localIso) {
    // verschil tussen de gegeven lokale tijd (tijdzone van Home Assistant) en UTC, in ms
    return Date.parse(localIso + "Z") - this._localToMs(localIso);
  }
  _localToMs(localIso) {
    // lokale tijd (JJJJ-MM-DDTUU:MM[:SS], tijdzone van Home Assistant) naar een tijdstip in ms.
    // Twee rondes zodat ook de dagen van de zomer- en wintertijd kloppen (laatste zondag van maart
    // en oktober); bij het dubbele uur in oktober kiezen we het eerste, net als Home Assistant (fold=0).
    const iso = localIso.length === 16 ? localIso + ":00" : localIso;
    const alsUtc = Date.parse(iso + "Z");
    const off = (t) => Date.parse(this._localInput(t) + ":00Z") - Math.floor(t / 60000) * 60000;
    let t = alsUtc - off(alsUtc);
    const o2 = off(t);
    if (alsUtc - o2 !== t) { const t2 = alsUtc - o2; if (this._localInput(t2) === iso.slice(0, 16)) t = t2; }
    const vroeger = t - 3600000;
    if (this._localInput(vroeger) === iso.slice(0, 16)) t = vroeger;
    if (this._localInput(t) !== iso.slice(0, 16)) {
      // uur dat niet bestaat (zomertijd, 02u00 tot 03u00): zoals Home Assistant met de afstand van vóór de wissel,
      // 02u30 wordt dus 03u30
      t = alsUtc - off(alsUtc - 6 * 3600000);
    }
    return t;
  }
  _openShare(e, note) {
    const p = this.shadowRoot.getElementById("panel");
    this._message("");
    this._form = { type: "share", entry: e };
    p.innerHTML = `<div class="share"><h3>Code delen: ${esc(e.name)}</h3>
      ${note ? `<div class="row msg ok">${esc(note)}</div>` : ""}
      <div class="row" style="display:block"><textarea id="stext">${esc(this._shareText(e))}</textarea></div>
      <div class="row sendbar">
        ${deelKnoppenHtml()}
        <button class="btn" id="cancel">Sluiten</button></div>
      <div class="row muted">De tekst kun je hierboven nog aanpassen. WhatsApp, sms en mail openen met de tekst klaar; je kiest daar zelf de ontvanger.</div></div>`;
    const $ = (id) => p.querySelector("#" + id);
    deelKnoppenKoppelen(this, p, "Je toegangscode voor Btechnics");
    $("cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
    if (!this._mobile) p.scrollIntoView({ block: "nearest" });
  }
  _openForm(type, e, act, text) {
    if (!this._data) return this._message("De codes zijn nog niet geladen.");
    const p = this.shadowRoot.getElementById("panel");
    const doors = this._data.doors;
    this._message("");
    this._form = { type, entry: e };
    if (type === "confirm") {
      p.innerHTML = `<h3>Bevestigen</h3><div class="row">${text}</div>
        <div class="row"><button class="btn danger" id="ok">Bevestigen</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => this._run({ action: act, entry: e.id }, act === "retire" ? (this._userMode ? "Gestopt" : "Uit dienst gehaald") : "Definitief verwijderd"));
    } else if (type === "block") {
      // standaard morgen om dit uur, in de tijdzone van Home Assistant (niet die van de browser)
      const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: (this._hass.config || {}).time_zone || undefined,
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
        .formatToParts(new Date(Date.now() + 86400000)).filter((p) => p.type !== "literal").map((p) => [p.type, p.value]));
      const def = `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
      p.innerHTML = `<h3>${e.status === "blocked" ? "Blokkering aanpassen" : "Blokkeren"}: ${esc(e.name)}</h3>
        <div class="row"><label><input type="radio" name="bm" value="until" checked> Tot</label><input id="until" type="datetime-local" value="${def}"></div>
        <div class="row"><label><input type="radio" name="bm" value="manual"> Tot ik deblokkeer</label></div>
        <div class="row muted">De ${e.kind === "badge" ? "badge" : "code"} werkt binnen enkele seconden niet meer op de tablets. Met een einddatum komt ze daarna vanzelf terug.</div>
        <div class="row"><button class="btn primary" id="ok">Blokkeren</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelector("#ok").addEventListener("click", () => {
        const manual = p.querySelector("input[name=bm]:checked").value === "manual";
        const until = p.querySelector("#until").value;
        if (!manual && !until) return this._message("Kies een datum en uur.");
        // zonder tijdzone doorgeven: Home Assistant leest dit in zijn eigen tijdzone (die van de kaart), niet die van de browser
        this._run({ action: "block", entry: e.id, ...(manual ? {} : { until: until.length === 16 ? until + ":00" : until }) }, "Geblokkeerd");
      });
    } else if (type === "addbadge") {
      const unknown = (this._data.unknown_cards || []);
      p.innerHTML = `<h3>Nieuwe badge</h3>
        <div class="row"><label>Naam <input id="name" type="text"></label>
        <label>Badgenummer <input id="card" type="text" maxlength="16" placeholder="bv. 3CFC52F1" style="font-family:monospace"></label></div>
        ${unknown.length ? `<div class="row">Onlangs geweigerd aan een lezer: ${unknown.map((u) => `<button class="btn" data-card="${esc(u.card)}" title="${esc(u.doors.join(", "))}, ${u.count} keer">${esc(u.card)} <span class="muted">${esc(this._fmt.dateTime.format(new Date(u.last * 1000)))}</span></button>`).join("")}</div>`
          : `<div class="row muted">Tip: hou de nieuwe badge eerst voor een lezer. Ze wordt geweigerd, en verschijnt hier dan met haar nummer.</div>`}
        <div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" value="${esc(d.id)}"> ${esc(d.name)}</label>`).join("")}</div>
        <div class="row muted">De badge krijgt dezelfde rechten als de bestaande badges en codes.</div>
        <div class="row"><button class="btn primary" id="ok">Opslaan</button><button class="btn" id="cancel">Annuleren</button></div>`;
      p.querySelectorAll("[data-card]").forEach((b) => b.addEventListener("click", () => { p.querySelector("#card").value = b.dataset.card; }));
      p.querySelector("#ok").addEventListener("click", () => {
        const name = p.querySelector("#name").value.trim();
        const card = p.querySelector("#card").value.trim().toUpperCase();
        const sel = [...p.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
        if (!name) return this._message("Geef een naam.");
        if (!/^[0-9A-F]{4,16}$/.test(card)) return this._message("Een badgenummer heeft 4 tot 16 tekens (cijfers en A tot F).");
        if (!sel.length) return this._message("Kies minstens een deur.");
        this._run({ action: "add_badge", name, card, doors: sel }, "Badge toegevoegd");
      });
    } else {
      const isNew = type === "add";
      const isBadge = e && e.kind === "badge";
      const active = isNew || e.status === "active";
      const on = new Set(isNew ? [] : e.doors.map((d) => d.id));
      const waiting = !isNew && this._waiting(e);
      const canValid = isNew || e.status !== "retired";
      const vast = isNew || e.soort !== "tijdelijk";
      const vn0 = isNew ? "" : (e.voornaam || (e.achternaam ? "" : e.name)), an0 = isNew ? "" : (e.achternaam || "");
      this._blokken = isNew ? [] : (e.schema || []).map((b) => ({ ...b, dagen: [...b.dagen] }));
      p.innerHTML = `<h3>${isNew ? "Nieuwe code" : "Aanpassen: " + esc(e.name)}</h3>
        ${vast ? `<div class="row"><label>Voornaam <input id="vn" type="text" value="${esc(vn0)}" autocomplete="off"></label><label>Naam <input id="an" type="text" value="${esc(an0)}" autocomplete="off"></label></div>
          <div class="row"><label>Gsm <input id="tel" type="tel" value="${isNew ? "" : esc(e.telefoon || "")}"></label><label>E-mail <input id="mail" type="email" value="${isNew ? "" : esc(e.email || "")}"></label></div>` : ""}
        <div class="row">${vast ? "" : `<label>Naam <input id="name" type="text" value="${isNew ? "" : esc(e.name)}"></label>`}
        ${isBadge ? "" : `<label>Code <input id="code" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="8" placeholder="${isNew ? "4 tot 8 cijfers" : "ongewijzigd"}"></label>`}</div>
        ${!isBadge && active ? `<div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" value="${esc(d.id)}" ${on.has(d.id) ? "checked" : ""}> ${esc(d.name)}</label>`).join("")}</div>` : ""}
        ${!isBadge && waiting ? `<div class="row">Deuren: ${e.stored.map((d) => `<span class="chip">${esc(d.name)}</span>`).join("")}</div>` : ""}
        ${vast && e && e.foto ? `<div class="row">${this._avatar(e, 72)}<span class="muted">Foto uit het portaal.</span></div>` : ""}
        ${vast ? `<div class="row" style="display:block"><div class="muted" style="margin-bottom:4px">Toegangsuren</div><div id="blokken"></div>
          <button class="btn" id="blok" type="button"><ha-icon icon="mdi:clock-plus-outline" style="--mdc-icon-size:18px"></ha-icon> Uren toevoegen</button>
          <span class="muted" style="margin-left:8px">Geen uren is altijd. Een eindtijd voor de begintijd loopt door tot de volgende dag.</span></div>` : ""}
        ${canValid ? this._validityRows(e) : ""}
        ${!isNew && !active && !waiting ? `<div class="row muted">Deze ${isBadge ? "badge" : "code"} werkt nu niet. De aanpassing geldt zodra ze weer actief is.</div>` : ""}
        <div class="row"><button class="btn primary" id="ok">Opslaan</button><button class="btn" id="cancel">Annuleren</button></div>`;
      if (vast) this._tekenBlokken(p);
      p.querySelector("#ok").addEventListener("click", () => {
        const vn = vast ? p.querySelector("#vn").value.trim() : "", an = vast ? p.querySelector("#an").value.trim() : "";
        const name = vast ? `${vn} ${an}`.trim() : p.querySelector("#name").value.trim();
        const tel = vast ? p.querySelector("#tel").value.trim() : "", mail = vast ? p.querySelector("#mail").value.trim() : "";
        const blokken = vast ? this._blokken : null;
        if (blokken && blokken.some((b) => !b.dagen.length || !b.van || !b.tot)) return this._message("Elk blok toegangsuren heeft minstens één dag, een begin en een einde.");
        if (mail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(mail)) return this._message("Dat e-mailadres klopt niet.");
        const code = p.querySelector("#code") ? p.querySelector("#code").value.trim() : "";
        const sel = [...p.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
        if (!name) return this._message("Geef een naam.");
        if (code && !/^[0-9]{4,8}$/.test(code)) return this._message("Een code heeft 4 tot 8 cijfers.");
        const curVu = !isNew && e.valid_until ? this._localInput(Date.parse(e.valid_until)) + ":00" : "";
        const v = canValid ? this._readValidity(p, curVu) : { vf: "", vu: "" };
        if (v.error) return this._message(v.error);
        if (isNew) {
          if (!code) return this._message("Geef een code.");
          if (!sel.length) return this._message("Kies minstens een deur.");
          const add = { action: "add", name, code, doors: sel, voornaam: vn, achternaam: an, telefoon: tel, email: mail, schema: blokken || [] };
          if (v.vf) add.valid_from = v.vf;
          if (v.vu) add.valid_until = v.vu;
          return this._run(add, "Code toegevoegd", (res) => {
            const id = res && res.result && res.result.id;
            const ne = id && this._data.entries.find((x) => x.id === id);
            if (ne) this._openShare(ne, "Code toegevoegd. Deel ze meteen:");
          });
        }
        const steps = [];
        const cur = { vf: e.valid_from ? this._localInput(Date.parse(e.valid_from)) + ":00" : "", vu: e.valid_until ? this._localInput(Date.parse(e.valid_until)) + ":00" : "" };
        const vmsg = canValid && (v.vf !== cur.vf || v.vu !== cur.vu) ? { action: "validity", entry: e.id, valid_from: v.vf, valid_until: v.vu } : null;
        if (isBadge) {
          if (name !== e.name) steps.push({ action: "rename_badge", entry: e.id, name });
          if (vmsg) steps.push(vmsg);
          if (!steps.length) return this._message("Er is niets gewijzigd.");
          return this._run(steps, "Aangepast");
        }
        const msg = { action: "update", entry: e.id };
        if (vast) {
          if (vn !== (e.voornaam || "") || an !== (e.achternaam || "")) { msg.voornaam = vn; msg.achternaam = an; }
          if (tel !== (e.telefoon || "")) msg.telefoon = tel;
          if (mail !== (e.email || "")) msg.email = mail;
          if (JSON.stringify(blokken) !== JSON.stringify(e.schema || [])) msg.schema = blokken;
        } else if (name !== e.name) msg.name = name;
        if (code) msg.code = code;
        if (active) {
          if (!sel.length) return this._message("Kies minstens een deur. Wil je de code overal weg, gebruik dan Uit dienst.");
          const now = e.doors.map((d) => d.id).sort().join(",");
          if (sel.slice().sort().join(",") !== now) msg.doors = sel;
        }
        if (Object.keys(msg).length > 2) steps.push(msg);
        if (vmsg) steps.push(vmsg);
        if (!steps.length) return this._message("Er is niets gewijzigd.");
        this._run(steps, "Aangepast");
      });
    }
    p.querySelector("#cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
    if (!this._mobile) p.scrollIntoView({ block: "nearest" });
    this._fotos();
  }
  _avatar(e, size) {
    const st = size ? ` style="width:${size}px;height:${size}px;font-size:${Math.round(size / 2.6)}px"` : "";
    return e && e.foto ? `<span class="av"${st}><img data-fotoid="${esc(e.id)}" data-v="${esc(e.foto_v || 0)}" alt=""></span>`
      : `<span class="av"${st}>${esc(((e && e.name) || "?").trim().charAt(0).toUpperCase())}</span>`;
  }
  _fotos() {
    if (this._userMode) return;
    this.shadowRoot.querySelectorAll("img[data-fotoid]").forEach(async (img) => {
      if (img.dataset.done) return;
      img.dataset.done = "1";
      try { img.src = await fotoSrc(this._hass, img.dataset.fotoid, img.dataset.v); } catch (err) { img.remove(); }
    });
  }
  _tekenBlokken(p) {
    const el = p.querySelector("#blokken");
    const teken = () => {
      const bl = this._blokken;
      el.innerHTML = bl.length ? bl.map((b, i) => `<div class="blok"><span class="days">${WEEK.map(([n, l]) => `<button type="button" class="${b.dagen.includes(n) ? "on" : ""}" data-blok="${i}" data-dag="${n}">${l}</button>`).join("")}</span>
          <label>van <input type="time" data-blok="${i}" data-veld="van" value="${b.van}"></label><label>tot <input type="time" data-blok="${i}" data-veld="tot" value="${b.tot}"></label>
          <button type="button" class="btn danger" data-blokweg="${i}" title="Weg"><ha-icon icon="mdi:close" style="--mdc-icon-size:18px"></ha-icon></button></div>`).join("")
        : `<div class="muted" style="padding:2px 0 6px">Altijd toegang.</div>`;
    };
    teken();
    el.addEventListener("click", (ev) => {
      const dag = ev.target.closest("[data-dag]");
      if (dag) { const b = this._blokken[+dag.dataset.blok], n = +dag.dataset.dag; b.dagen = b.dagen.includes(n) ? b.dagen.filter((x) => x !== n) : [...b.dagen, n]; return teken(); }
      const w = ev.target.closest("[data-blokweg]");
      if (w) { this._blokken.splice(+w.dataset.blokweg, 1); teken(); }
    });
    el.addEventListener("input", (ev) => { const t = ev.target; if (t.dataset && t.dataset.veld) this._blokken[+t.dataset.blok][t.dataset.veld] = t.value; });
    p.querySelector("#blok").addEventListener("click", () => { this._blokken.push({ dagen: [1, 2, 3, 4, 5], van: "08:00", tot: "18:00" }); teken(); });
  }
  _linkPaneel(titel, note, tekst, onderwerp) {
    // zelfde deelvenster als bij een code: tekst aanpasbaar, WhatsApp, sms, mail, kopiëren
    const p = this.shadowRoot.getElementById("panel");
    this._form = { type: "share" };
    p.innerHTML = `<div class="share"><h3>${esc(titel)}</h3>${note ? `<div class="row msg ok">${esc(note)}</div>` : ""}
      <div class="row" style="display:block"><textarea id="stext">${esc(tekst)}</textarea></div>
      <div class="row sendbar">${deelKnoppenHtml()}<button class="btn" id="cancel">Sluiten</button></div>
      <div class="row muted">Deze link is persoonlijk: wie hem heeft, kan de pagina openen. Wijzigen vraagt altijd de eigen code.</div></div>`;
    const $ = (id) => p.querySelector("#" + id);
    deelKnoppenKoppelen(this, p, onderwerp);
    $("cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
    if (!this._mobile) p.scrollIntoView({ block: "nearest" });
  }
  async _portaalLink(e) {
    if (e.portaal && !confirm(`${e.name} heeft al een portaallink. Een nieuwe link maken? De oude werkt dan niet meer.`)) return;
    try {
      const r = await this._ws({ type: "btechnics_toegang/portaal/link", entry: e.id });
      const hi = e.voornaam || e.name;
      this._linkPaneel(`Portaallink: ${e.name}`, "Nieuwe link gemaakt. Stuur hem door:",
        `Hallo ${hi},\n\nVia deze persoonlijke link bekijk je je toegang bij Btechnics en pas je zelf je gsm-nummer, e-mail, foto of code aan:\n${r.url}\n\nBewaar de link en deel hem met niemand.`,
        "Je persoonlijke toegangspagina bij Btechnics");
      this._load();
    } catch (err) { this._message(`Niet gelukt: ${errText(err)}`); }
  }
  _openInvite() {
    if (!this._data) return this._message("De codes zijn nog niet geladen.");
    const p = this.shadowRoot.getElementById("panel");
    const doors = this._data.doors;
    this._message("");
    this._form = { type: "invite" };
    this._blokken = [];
    p.innerHTML = `<h3>Uitnodiging voor een medewerker</h3>
      <div class="row muted">De medewerker krijgt een link en vult zelf voornaam, naam, gsm, e-mail, zijn code en een foto in. De code werkt meteen na het invullen.</div>
      <div class="row"><label>Naam (optioneel) <input id="iname" type="text" maxlength="40" placeholder="voor de begroeting"></label>
        <label>Link geldig <select id="idagen"><option value="1">1 dag</option><option value="3">3 dagen</option><option value="7" selected>7 dagen</option><option value="14">14 dagen</option></select></label></div>
      <div class="row">Deuren: ${doors.map((d) => `<label><input type="checkbox" class="id" value="${esc(d.id)}" ${doors.length === 1 ? "checked" : ""}> ${esc(d.name)}</label>`).join("")}</div>
      <div class="row" style="display:block"><div class="muted" style="margin-bottom:4px">Toegangsuren</div><div id="blokken"></div>
        <button class="btn" id="blok" type="button"><ha-icon icon="mdi:clock-plus-outline" style="--mdc-icon-size:18px"></ha-icon> Uren toevoegen</button></div>
      ${this._validityRows(null)}
      <div class="row"><button class="btn primary" id="ok">Maak link en deel</button><button class="btn" id="cancel">Annuleren</button></div>`;
    this._tekenBlokken(p);
    p.querySelector("#ok").addEventListener("click", async () => {
      const sel = [...p.querySelectorAll(".id:checked")].map((x) => x.value);
      if (!sel.length) return this._message("Kies minstens een deur.");
      if (this._blokken.some((b) => !b.dagen.length || !b.van || !b.tot)) return this._message("Elk blok toegangsuren heeft minstens één dag, een begin en een einde.");
      const v = this._readValidity(p, "");
      if (v.error) return this._message(v.error);
      const naam = p.querySelector("#iname").value.trim();
      const msg = { type: "btechnics_toegang/uitnodiging", doors: sel, schema: this._blokken, dagen: Number(p.querySelector("#idagen").value), naam };
      if (v.vf) msg.valid_from = v.vf;
      if (v.vu) msg.valid_until = v.vu;
      try {
        const r = await this._ws(msg);
        const dn = doors.filter((d) => sel.includes(d.id)).map((d) => d.name);
        this._linkPaneel("Uitnodiging delen", "Link gemaakt. Stuur hem door:",
          `Hallo${naam ? " " + naam : ""},\n\nVia deze link maak je je toegang bij Btechnics aan (${dn.join(", ")}). Je vult je gegevens in, kiest je eigen code en neemt een foto. Het duurt twee minuten.\n${r.url}\n\nDe link werkt ${msg.dagen === 1 ? "1 dag" : msg.dagen + " dagen"} en maar een keer.`,
          "Je toegang bij Btechnics");
        this._load();
      } catch (err) { this._message(`Niet gelukt: ${errText(err)}`); }
    });
    p.querySelector("#cancel").addEventListener("click", () => this._closeForm());
    p.classList.add("on");
    if (!this._mobile) p.scrollIntoView({ block: "nearest" });
  }
  _closeForm() {
    const p = this.shadowRoot.getElementById("panel");
    p.classList.remove("on");
    p.innerHTML = "";
    this._form = null;
  }
  async _run(msgs, okText, after) {
    if (this._busy) return;
    this._busy = true;
    this.shadowRoot.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    this._message("Bezig...", true);
    let res = null, ok = false;
    try {
      for (const msg of Array.isArray(msgs) ? msgs : [msgs]) {
        let data = msg._ws ? { ...msg._ws } : { type: "btechnics_toegang/manage/action", ...msg };
        if (this._userMode && !msg._ws) {
          // gebruiker: eigen commando's (de grenzen worden in Home Assistant nagekeken)
          const { action, ...rest } = msg;
          data = { type: action === "add" ? "btechnics_toegang/user/add" : "btechnics_toegang/user/stop", ...rest };
        }
        Object.keys(data).forEach((k) => { if (data[k] === "" && !["voornaam", "achternaam", "telefoon", "email"].includes(k)) delete data[k]; });
        res = await this._ws(data);
      }
      this._closeForm();
      this._message(okText, true);
      ok = true;
    } catch (e) {
      this._message(`Niet gelukt: ${errText(e)}`);
    } finally {
      this._busy = false;
      await this._load();
      this.shadowRoot.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
    if (ok && after) after(res);
  }
  getCardSize() {
    return 10;
  }
  static getStubConfig() {
    return {};
  }
}


/* ------------------------------------------------------------------ handleiding (gedeelde opbouw)
   Opbouw: zoeken, "Wat wil je doen?" als tegels met korte stappen, uitleg per scherm inklapbaar, begrippen. */
// foutenlog van 60 dagen (enkel beheerders): waarschuwingen en fouten van deze integratie, ook na een herstart
function errorLogSection(root, hass, domain, dateTime) {
  const box = document.createElement("div");
  box.className = "errlog";
  box.innerHTML = `<h3 style="font-size:1.05rem;font-weight:500;margin:22px 0 8px">Foutenlog</h3>
    <p class="muted" style="margin:0 0 8px;font-size:0.9rem">Waarschuwingen en fouten van de laatste 60 dagen, ook na een herstart van Home Assistant. Dezelfde melding op dezelfde dag telt op.</p>
    <button class="btn" data-errlog>Foutenlog tonen</button><div class="errout"></div>`;
  root.appendChild(box);
  const out = box.querySelector(".errout"), btn = box.querySelector("[data-errlog]");
  const e2 = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  btn.addEventListener("click", async () => {
    if (out.dataset.open === "1") { out.innerHTML = ""; out.dataset.open = ""; btn.textContent = "Foutenlog tonen"; return; }
    btn.disabled = true;
    try {
      const r = await hass.callWS({ type: `${domain}/errors`, days: 60 });
      const t = r.totals || {};
      out.innerHTML = `<p style="margin:10px 0">${r.rows.length ? `<b>${(t.ERROR || 0) + (t.CRITICAL || 0)}</b> fouten en <b>${t.WARNING || 0}</b> waarschuwingen in 60 dagen.` : "Geen waarschuwingen of fouten in de laatste 60 dagen."}</p>
        ${r.rows.length ? `<div class="scroll"><table><thead><tr><th>Laatst</th><th>Niveau</th><th>Onderdeel</th><th>Melding</th><th class="num">Aantal</th></tr></thead><tbody>
        ${r.rows.map((x) => `<tr><td style="white-space:nowrap">${e2(dateTime(x.last))}</td><td>${x.level === "WARNING" ? "waarschuwing" : "fout"}</td><td>${e2(x.source)}</td>
          <td>${x.details ? `<details><summary>${e2(x.message)}</summary><pre style="white-space:pre-wrap;font-size:0.8rem;margin:6px 0 0">${e2(x.details)}</pre></details>` : e2(x.message)}</td>
          <td class="num">${x.count}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
      out.dataset.open = "1"; btn.textContent = "Foutenlog verbergen";
    } catch (e) {
      out.innerHTML = `<div class="error">Foutenlog laden mislukt: ${e2((e && e.message) || e)}</div>`;
    } finally { btn.disabled = false; }
  });
}

const HELP_CSS = `
  .hp { max-width: 980px; line-height: 1.5; }
  .hp-search { width: 100%; max-width: 420px; margin: 0 0 14px; }
  .hp h3 { font-size: 1.05rem; font-weight: 500; margin: 18px 0 10px; }
  .hp-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
  .hp-tile { display: flex; gap: 10px; align-items: flex-start; text-align: left; font: inherit; color: var(--primary-text-color);
    background: var(--secondary-background-color); border: 1px solid transparent; border-radius: 10px; padding: 12px; cursor: pointer; }
  .hp-tile:hover, .hp-tile.on { border-color: var(--primary-color); }
  .hp-tile ha-icon { color: var(--primary-color); flex: none; --mdc-icon-size: 22px; }
  .hp-tile b { display: block; font-weight: 500; }
  .hp-tile span { font-size: 0.85rem; color: var(--secondary-text-color); }
  .hp-steps { border: 1px solid var(--primary-color); border-radius: 10px; padding: 12px 16px; margin: 12px 0 4px; }
  .hp-steps h4 { margin: 0 0 6px; font-size: 1rem; font-weight: 500; display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .hp-steps ol { margin: 4px 0 6px; padding-left: 22px; }
  .hp-steps li { margin: 3px 0; }
  .hp-steps .hp-tip { font-size: 0.85rem; color: var(--secondary-text-color); margin: 4px 0 0; }
  .hp details { border-bottom: 1px solid var(--divider-color); }
  .hp summary { cursor: pointer; padding: 10px 2px; list-style: none; display: flex; gap: 10px; align-items: baseline; }
  .hp summary::-webkit-details-marker { display: none; }
  .hp summary::before { content: "+"; width: 14px; color: var(--primary-color); font-weight: 600; flex: none; }
  .hp details[open] summary::before { content: "\\2212"; }
  .hp summary b { font-weight: 500; }
  .hp summary span { font-size: 0.85rem; color: var(--secondary-text-color); }
  .hp .body { padding: 0 2px 12px 24px; }
  .hp .body p { margin: 4px 0 8px; }
  .hp .body td:first-child, .hp-terms td:first-child { white-space: nowrap; font-weight: 500; width: 1%; }
  .hp .hidden { display: none; }
  .hp .none { color: var(--secondary-text-color); padding: 8px 0; }
  @media (max-width: 640px) { .hp .body td:first-child, .hp-terms td:first-child { white-space: normal; } .hp .body { padding-left: 6px; } }
`;
const hpTable = (rows) => `<div class="scroll"><table><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
function helpHtml(doc) {
  return `<div class="hp">
    <input class="hp-search" type="search" placeholder="Zoek in de handleiding, ${doc.hint || ""}" aria-label="Zoek in de handleiding">
    <h3>Wat wil je doen?</h3>
    <div class="hp-tiles">${doc.tasks.map((t, i) => `<button class="hp-tile" data-task="${i}"><ha-icon icon="${t.icon}"></ha-icon><div><b>${t.title}</b><span>${t.sub}</span></div></button>`).join("")}</div>
    <div class="hp-steps hidden"></div>
    <div class="none hidden">Niets gevonden.</div>
    <h3>Uitleg per scherm</h3>
    ${doc.topics.map((t) => `<details data-topic><summary><b>${t.title}</b><span>${t.sub}</span></summary><div class="body">${t.html}</div></details>`).join("")}
    <h3>Begrippen</h3>
    <div class="hp-terms">${hpTable(doc.terms)}</div>
    ${doc.foot ? `<p class="muted" style="font-size:0.8rem;margin-top:12px">${doc.foot}</p>` : ""}
  </div>`;
}
function wireHelp(root, doc, openTab) {
  const steps = root.querySelector(".hp-steps");
  const show = (i) => {
    const t = doc.tasks[i];
    root.querySelectorAll(".hp-tile").forEach((b) => b.classList.toggle("on", Number(b.dataset.task) === i));
    steps.innerHTML = `<h4><span>${t.title}</span>${t.tab ? `<button class="btn" data-open-tab="${t.tab}">Naar ${t.tabName || t.tab} &rsaquo;</button>` : ""}</h4>
      <ol>${t.steps.map((s) => `<li>${s}</li>`).join("")}</ol>${t.tip ? `<p class="hp-tip">${t.tip}</p>` : ""}`;
    steps.classList.remove("hidden");
    const ob = steps.querySelector("[data-open-tab]");
    if (ob) ob.addEventListener("click", () => openTab(ob.dataset.openTab));
    steps.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  root.querySelectorAll(".hp-tile").forEach((b) => b.addEventListener("click", () => show(Number(b.dataset.task))));
  const text = (html) => html.replace(/<[^>]+>/g, " ").toLowerCase();
  const input = root.querySelector(".hp-search");
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    let hits = 0;
    root.querySelectorAll(".hp-tile").forEach((b) => {
      const t = doc.tasks[Number(b.dataset.task)];
      const ok = !q || text(`${t.title} ${t.sub} ${t.steps.join(" ")} ${t.tip || ""}`).includes(q);
      b.classList.toggle("hidden", !ok); hits += ok;
    });
    root.querySelectorAll("details[data-topic]").forEach((d, i) => {
      const t = doc.topics[i];
      const ok = !q || text(`${t.title} ${t.sub} ${t.html}`).includes(q);
      d.classList.toggle("hidden", !ok); d.open = !!q && ok; hits += ok;
    });
    if (q) steps.classList.add("hidden");
    root.querySelector(".none").classList.toggle("hidden", hits > 0);
  });
}

const RIGHTS_TABLE = hpTable([["<b>Onderdeel</b>", "<b>Beheerder</b>", "<b>Gebruiker</b>"],
  ["Overzicht", "Alles: laatste toegangen met naam en uur, Deur openen", "Status en aantallen van vandaag"],
  ["Historiek", "Alles: zoeken, per persoon, per dag, CSV", "Enkel aantallen per dag en per deur, zonder namen of uren"],
  ["Tijdelijke code", "Ja, onbeperkt", "Ja: code door Home Assistant gekozen, maximaal 7 dagen geldig, maximaal 10 tegelijk"],
  ["Eigen tijdelijke codes", "Ja", "Opnieuw delen en stoppen zolang ze lopen"],
  ["Codes van anderen", "Ja", "Nee, ook niet de aantallen"],
  ["Vaste codes, uitnodigingen, blokkeren, uit dienst", "Ja", "Nee"],
  ["Deuren en tablets", "Ja", "Nee"],
  ["Wijzigingen", "Ja, met de naam van wie iets deed (ook gebruikers en het portaal)", "Nee"]]);
const HELP_DOC = {
  hint: "bv. tijdelijke code of uitnodiging",
  tasks: [
    { icon: "mdi:timer-outline", title: "Tijdelijke code geven", sub: "Pakket, technicus of gast", tab: "codes", tabName: "Codes",
      steps: ["Klik op <b>Tijdelijke code</b> in Codes. Op de gsm: de oranje knop <b>Tijdelijke code</b> rechtsonder, op elk tabblad.", "Kies <b>Pakket</b>, <b>Technicus</b>, <b>Gast</b> of <b>Andere</b>; pas de naam aan, bv. \"Pakket bol\".", "Vink de deur of deuren aan (je laatste keuze wordt onthouden).", "Kies hoe lang: 1 uur, Vandaag, 24 uur, 3 dagen, 1 week, of <b>Van … tot …</b> voor een eigen begin en einde.", "Klik op <b>Maak code en deel</b> en kies WhatsApp, Sms, Mail of Kopieer tekst."],
      tip: "Home Assistant kiest zelf een willekeurige, vrije code van 6 cijfers. Na het einde gaat ze vanzelf uit dienst. Alle lopende tijdelijke codes vind je in de tab Tijdelijk." },
    { icon: "mdi:email-fast-outline", title: "Nieuwe medewerker uitnodigen", sub: "Hij vult zelf alles in", tab: "codes", tabName: "Codes",
      steps: ["Klik op <b>Uitnodigen</b> in Codes (op de gsm: <b>Nieuw</b>, dan <b>Uitnodiging</b>).", "Vink de deuren aan, voeg eventueel toegangsuren en een geldigheid toe.", "Klik op <b>Maak link en deel</b> en stuur de link via WhatsApp, Sms of Mail.", "De medewerker vult voornaam, naam, gsm, e-mail, zijn eigen code en een foto in. De code werkt meteen; jij krijgt een melding in Home Assistant."],
      tip: "De link werkt maar een keer en vervalt na de gekozen tijd. Openstaande uitnodigingen staan onder de lijst, met Intrekken." },
    { icon: "mdi:account-key", title: "Vaste code toevoegen", sub: "Zelf invullen voor iemand", tab: "codes", tabName: "Codes",
      steps: ["Open <b>Codes</b> en klik op <b>Nieuwe code</b> (op de gsm: <b>Nieuw</b>, dan <b>Vaste code</b>).", "Vul voornaam, naam en een code van 4 tot 8 cijfers in; gsm en e-mail mogen erbij.", "Vink de deuren aan en kies eventueel toegangsuren.", "Eventueel <b>Geldig vanaf</b> en <b>Geldig tot</b>; leeg = vanaf nu, zonder einde.", "Klik op <b>Opslaan</b> en deel de code in het venster dat opent."] },
    { icon: "mdi:link-variant", title: "Portaallink sturen", sub: "Medewerker past zelf zijn gegevens aan", tab: "codes", tabName: "Codes",
      steps: ["Zoek de persoon en klik op <b>Portaallink</b> (op de gsm: tik op de rij).", "Er komt een nieuwe link; een vorige link werkt dan niet meer.", "Stuur hem door. Op die pagina past de medewerker gsm, e-mail, foto of code aan; elke wijziging vraagt zijn huidige code."] },
    { icon: "mdi:share-variant", title: "Een code (opnieuw) delen", sub: "WhatsApp, sms, mail of kopieer", tab: "codes", tabName: "Codes",
      steps: ["Zoek de persoon in <b>Codes</b>.", "Klik op <b>Delen</b> (op de gsm: tik op de rij, dan <b>Delen</b>).", "Pas de tekst eventueel aan en kies WhatsApp, Sms, Mail of Kopieer tekst. De ontvanger kies je daar zelf."],
      tip: "De tekst vermeldt de deur(en), de geldigheid en hoe je opent: de code typen op het scherm naast de deur." },
    { icon: "mdi:lock-clock", title: "Iemand tijdelijk blokkeren", sub: "Tot een datum of tot je deblokkeert", tab: "codes", tabName: "Codes",
      steps: ["Zoek de persoon en klik op <b>Blokkeren</b> (op de gsm: tik op de rij).", "Kies <b>Tot</b> een datum en uur, of <b>Tot ik deblokkeer</b>.", "Klik op <b>Blokkeren</b>: de code werkt binnen enkele seconden niet meer op de tablets.", "Met een einddatum komt ze vanzelf terug; anders klik je later op <b>Deblokkeren</b>."] },
    { icon: "mdi:account-cancel", title: "Iemand de toegang afnemen", sub: "Uit dienst, later herstelbaar", tab: "codes", tabName: "Codes",
      steps: ["Zoek de persoon en klik op <b>Uit dienst</b> (op de gsm: tik op de rij, dan <b>Uit dienst zetten</b> onderaan in rood).", "Bevestig. De code werkt niet meer en wordt bewaard.", "Terug nodig? Tab <b>Uit dienst</b>, klik op <b>Herstellen</b>. Definitief weg (met foto): <b>Definitief verwijderen</b>."] },
    { icon: "mdi:door", title: "Deur of tablet toevoegen", sub: "Nieuwe deur, tablet koppelen", tab: "deuren", tabName: "Deuren",
      steps: ["Open <b>Deuren</b> en klik op <b>Nieuwe deur</b>.", "Geef een naam en kies waarmee de deur opent (script, schakelaar of slot).", "Klik bij de deur op <b>Tablet koppelen</b>: er verschijnt een code van 6 cijfers, 10 minuten geldig.", "Geef die code in op de tablet onder Beheer, Koppelen."] },
    { icon: "mdi:door-open", title: "Deur openen op afstand", sub: "Vanop de pagina Overzicht", tab: "overzicht", tabName: "Overzicht",
      steps: ["Open <b>Overzicht</b>.", "Klik bij de juiste deur op <b>Deur openen</b>.", "De knop wordt rood: klik binnen 6 seconden nog eens.", "\"... is geopend.\" verschijnt. In de historiek staat Op afstand met je naam."],
      tip: "Enkel voor beheerders. Elke opening op afstand staat bij Wijzigingen." },
    { icon: "mdi:history", title: "Nagaan wie binnenkwam", sub: "Zoeken, per persoon, CSV", tab: "historiek", tabName: "Historiek",
      steps: ["Open <b>Historiek</b>.", "Zoek op naam of kies een deur, geopend of geweigerd, en een periode. Op de gsm staan die achter de knop <b>Filters</b>.", "Klik op <b>Per persoon</b> voor een overzicht per persoon; klik op een naam om te filteren.", "Klik op <b>Per dag</b> voor een lijst per dag; klik op een dag voor alle toegangen van die dag.", "<b>CSV</b> geeft alles voor Excel."] },
  ],
  topics: [
    { title: "Overzicht", sub: "Status per deur, laatste toegang, deur openen", html: hpTable([["Kaart per deur", "Online als minstens een tablet de laatste 3 minuten iets liet weten; laatste toegang (wie, hoe, wanneer), aantallen van vandaag, actieve codes en tablets online"], ["Deur openen", "Enkel beheerders, met bevestiging. Kan pas als bij Deuren gekozen is waarmee de deur opent"], ["Gebruikers", "Zien enkel de status en de aantallen van vandaag"], ["Recente toegangen", "De laatste 6 van die deur: tijd, naam, icoon voor de manier, vinkje geopend of kruisje geweigerd"]]) },
    { title: "Historiek", sub: "Toegangen van de laatste 400 dagen", html: `<p>Filters: zoeken, deur, status, periode (vandaag tot 1 jaar of eigen). Weergave Toegangen, Per persoon of Per dag. CSV voor Excel.</p>` + hpTable([["Op afstand", "Geopend met Deur openen; bij Hoe staat wie"], ["Foute code", "Een code die niet bestaat of niet geldig is voor die deur of dat uur"], ["Onbekend gezicht", "Een gezicht dat de tablet niet herkende"], ["Exitknop", "Geopend met de knop binnen"]]) },
    { title: "Codes", sub: "Statussen en knoppen", html: hpTable([["Alles (tab)", "Alle codes, ook uit dienst"], ["Code tonen", "Klik op de puntjes om die ene code te zien; Codes tonen toont ze allemaal"], ["Actief", "Staat op de tablets en werkt"], ["Tijdelijk (tab)", "Alle codes met een geldigheid die nog niet uit dienst zijn, ook de wachtende"], ["Wacht op begin", "Geldigheid begint later; komt er vanzelf op (of Nu al activeren)"], ["Geblokkeerd", "Tijdelijk niet, tot een tijdstip of tot deblokkeren"], ["Uit dienst", "Werkt niet meer, bewaard en herstelbaar"], ["Toegangsuren", "Dagen en uren waarop een code werkt; zonder uren altijd"], ["Wijzigingen", "Onderaan: wie wat wanneer deed, ook de planner, het portaal en openen op afstand"]]) },
    { title: "Portaal", sub: "Uitnodiging en persoonlijke pagina", html: hpTable([["Uitnodiging", "Link voor een nieuwe medewerker: gegevens, eigen code (4 tot 8 cijfers, geen reeksen), verplichte foto. Een keer bruikbaar"], ["Persoonlijke pagina", "Na het aanmelden krijgt de medewerker een vaste link om later gsm, e-mail, foto of code aan te passen; altijd met zijn huidige code"], ["Te veel fouten", "Na 10 foute pogingen werkt de link niet meer; maak een nieuwe Portaallink"], ["Privacy", "Gegevens en foto blijven op deze Home Assistant. Definitief verwijderen wist ook de foto"]]) },
    { title: "Aan de deur", sub: "Hoe open je", html: hpTable([["Code", "Typ de code op het scherm van de tablet naast de deur"], ["Gezicht", "Binnenkort, vrijwillig, via de persoonlijke pagina"]]) },
    { title: "Rechten", sub: "Wat ziet een gebruiker zonder beheerrechten", html: `<p>Beheerders zijn de gebruikers met de optie Beheerder in Home Assistant (Instellingen, Personen). Alle anderen zijn gebruikers.</p>` + RIGHTS_TABLE },
    { title: "Op de gsm", sub: "Zelfde mogelijkheden, ander uitzicht", html: hpTable([["Oranje knop", "<b>Tijdelijke code</b> rechtsonder op Overzicht, Historiek en Codes"], ["Codes", "Een rij per persoon, zonder knoppen. Tik op de rij voor Code tonen, Delen, Portaallink, Aanpassen, Blokkeren, Geschiedenis; Uit dienst staat apart onderaan in rood. <b>Nieuw</b> voor een vaste code of uitnodiging"], ["Historiek", "Lijst per dag: uur, naam, deur en manier. Filters achter de knop <b>Filters</b>"], ["Wanneer", "Zodra de kaart smaller is dan 600 pixels"]]) },
    { title: "Goed om te weten", sub: "Snelheid en veiligheid", html: hpTable([["Snelheid", "Een wijziging staat binnen enkele seconden op de tablets"], ["Offline", "Een tablet zonder verbinding werkt verder met de laatste lijst en stuurt zijn logboek later door"], ["Codes", "De tablets krijgen enkel een hash van elke code, nooit de code zelf"], ["Archief", "Home Assistant bewaart elke toegang 400 dagen"]]) },
  ],
  terms: [
    ["Code", "4 tot 8 cijfers, intikken op de tablet"], ["Tijdelijke code", "6 cijfers, door Home Assistant gekozen, met een einde"],
    ["Geldigheid", "Van en tot wanneer een code werkt"], ["Toegangsuren", "Op welke dagen en uren een code werkt"],
    ["Uit dienst", "Werkt niet meer, maar bewaard en herstelbaar"], ["Planner", "Home Assistant zelf: zet codes aan en uit op het juiste moment"],
    ["Portaal", "Pagina waar een medewerker zelf zijn gegevens ingeeft"],
  ],
};

// handleiding voor een gebruiker zonder beheerrechten: enkel wat hij zelf kan doen
const HELP_USER = {
  hint: "bv. tijdelijke code",
  tasks: [
    { icon: "mdi:timer-outline", title: "Tijdelijke code maken en delen", sub: "Pakket, technicus of gast", tab: "codes", tabName: "Tijdelijke codes",
      steps: ["Klik op <b>Tijdelijke code</b>. Op de gsm: de oranje knop rechtsonder, op elk tabblad.", "Kies <b>Pakket</b>, <b>Technicus</b>, <b>Gast</b> of <b>Andere</b>; pas de naam aan, bv. \"Pakket bol\".", "Vink de deur of deuren aan.", "Kies hoe lang: 1 uur, Vandaag, 24 uur, 3 dagen, 1 week, of <b>Van … tot …</b> (maximaal 7 dagen).", "Klik op <b>Maak code en deel</b> en kies WhatsApp, Sms, Mail of Kopieer tekst."],
      tip: "Home Assistant kiest de code. Je kan maximaal 10 tijdelijke codes tegelijk actief hebben. Alles wat je doet komt met je naam bij de beheerders." },
    { icon: "mdi:share-variant", title: "Een tijdelijke code opnieuw delen", sub: "Zolang ze loopt", tab: "codes", tabName: "Tijdelijke codes",
      steps: ["Open <b>Tijdelijke codes</b>.", "Klik bij de code op <b>Delen</b> (op de gsm: tik op de rij, dan <b>Delen</b>).", "Kies WhatsApp, Sms, Mail of Kopieer tekst."], tip: "De code zelf staat enkel in de deeltekst." },
    { icon: "mdi:stop-circle-outline", title: "Een tijdelijke code stoppen", sub: "Werkt meteen niet meer", tab: "codes", tabName: "Tijdelijke codes",
      steps: ["Open <b>Tijdelijke codes</b>.", "Klik bij de code op <b>Stoppen</b> (op de gsm: tik op de rij) en bevestig."] },
    { icon: "mdi:calendar-month", title: "Zien hoeveel toegangen er waren", sub: "Per dag en per deur", tab: "historiek", tabName: "Historiek",
      steps: ["Open <b>Historiek</b>.", "Kies een deur en een periode.", "Je ziet per dag het aantal toegangen, geopend en geweigerd, en per deur."] },
  ],
  topics: [
    { title: "Wat mag ik als gebruiker?", sub: "Rechten in een tabel", html: RIGHTS_TABLE },
    { title: "Aan de deur", sub: "Hoe open je", html: hpTable([["Code", "Typ de code op het scherm van de tablet naast de deur"]]) },
  ],
  terms: [["Tijdelijke code", "Code van 6 cijfers met een begin en einde; na het einde werkt ze vanzelf niet meer"], ["Stoppen", "De code werkt meteen niet meer"]],
};

class VtoHandleiding extends VtoBase {
  _init() {
    const doc = isAdmin(this._hass) ? HELP_DOC : HELP_USER;
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${HELP_CSS}</style>
    <ha-card><div class="title">${esc(this._config.title || "Handleiding toegangscontrole")}</div><div id="hb">${helpHtml(doc)}</div></ha-card>`;
    wireHelp(this.shadowRoot.getElementById("hb"), doc, (view) => {
      // naar een andere pagina van hetzelfde dashboard
      const base = location.pathname.split("/").slice(0, 2).join("/");
      history.pushState(null, "", `${base}/${view}`);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    });
    if (isAdmin(this._hass)) errorLogSection(this.shadowRoot.getElementById("hb"), this._hass, "btechnics_toegang", (ts) => this._fmt.dateTime.format(new Date(ts * 1000)));
  }
  getCardSize() { return 12; }
  static getStubConfig() { return {}; }
}

// Home Assistant laadt deze module heel vroeg en installeert daarna een eigen custom element
// registry. Een kaart die we te vroeg registreren is daar niet zichtbaar ("Custom element doesn't
// exist"). Daarom registreren we opnieuw zolang het nodig is, telkens via window.customElements
// (het register dat NU actief is) en met een nieuwe subklasse (een constructor mag maar een keer).
const CARD_CLASSES = [["btechnics-toegang-overzicht", VtoOverzicht], ["btechnics-toegang-historiek", VtoToegang], ["btechnics-toegang-codes", VtoCodes], ["btechnics-toegang-handleiding", VtoHandleiding]];
const CARDS_VERSION = "0.4.5";
const define = (name, cls) => {
  if (window.customElements.get(name)) return;
  try {
    const R = class extends cls {};
    R.__btxToeg = CARDS_VERSION;
    window.customElements.define(name, R);
  } catch (e) { /* volgende poging */ }
};
// Een oude kopie van de pagina (service worker) kan eerst een oudere versie van dit script laden. Een custom
// element kan niet opnieuw gedefinieerd worden; daarom de al geregistreerde klasse op deze code laten steunen
// en bestaande kaarten opnieuw opbouwen (vastgesteld 26/09/2026: v0.3.8 uit de cache, v0.7.0 via de resource).
const reinit = (tag) => {
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.tagName === tag && el._hass) {
        el._fmt = formatters(el._hass.config && el._hass.config.time_zone);
        clearInterval(el._timer);
        el._timer = null;
        try { el._init(); } catch (e) { /* volgende kaart */ }
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
};
const olderThanMe = (ver) => {
  // enkel naar een nieuwere versie (twee scriptversies mogen elkaar niet om beurten vervangen)
  if (!ver) return true;
  const v = (x) => String(x || "0").split(".").map(Number);
  const [a, b] = [v(CARDS_VERSION), v(ver)];
  const i = a.findIndex((n, k) => n !== (b[k] || 0));
  return i >= 0 && a[i] > (b[i] || 0);
};
const liveClasses = (tag) => {
  // de klassen van de kaarten die echt op de pagina staan: met een scoped registry (HA frontend) is dat
  // niet altijd de klasse die customElements.get teruggeeft (vastgesteld 28/09/2026: v0.8.5 bleef staan)
  const out = new Set();
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.tagName === tag) out.add(el.constructor);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return out;
};
const upgrade = (name, cls) => {
  const cands = new Set([window.customElements.get(name), ...liveClasses(name.toUpperCase())]);
  let changed = false;
  for (const R of cands) {
    if (!R || !R.prototype || R.prototype instanceof cls || !olderThanMe(R.__btxToeg)) continue;
    try {
      Object.setPrototypeOf(R.prototype, cls.prototype);
      Object.setPrototypeOf(R, cls);
      R.__btxToeg = CARDS_VERSION;
      changed = true;
    } catch (e) { /* laat de oude versie staan */ }
  }
  if (changed) {
    // de vorige scriptversie liet eigen klikverwerking achter die we niet kunnen weghalen (dubbele acties):
    // een keer per versie de pagina herladen, anders de kaarten opnieuw opbouwen
    let again = false;
    try { again = sessionStorage.getItem("btxtoeg_reload") === CARDS_VERSION; if (!again) sessionStorage.setItem("btxtoeg_reload", CARDS_VERSION); } catch (e) { again = true; }
    if (!again) { location.reload(); return; }
    reinit(name.toUpperCase());
  }
};
const registerAll = () => { for (const [name, cls] of CARD_CLASSES) { define(name, cls); upgrade(name, cls); } };
registerAll();
let registerTries = 0;
const registerTimer = setInterval(() => { registerAll(); if (++registerTries >= 120) clearInterval(registerTimer); }, 500);
window.customCards = window.customCards || [];
for (const [type, name, description] of [
  ["btechnics-toegang-overzicht", "Btechnics Toegang overzicht", "Status van alle deuren met tablet"],
  ["btechnics-toegang-historiek", "Btechnics Toegang historiek", "Zoeken en filteren in een jaar toegangen, per persoon en per maand"],
  ["btechnics-toegang-codes", "Btechnics Toegang codes", "Codes, tijdelijke codes en uitnodigingen"],
  ["btechnics-toegang-handleiding", "Btechnics Toegang handleiding", "Uitleg bij de toegangscontrole"],
]) {
  if (!window.customCards.some((c) => c.type === type)) window.customCards.push({ type, name, description, preview: false });
}
