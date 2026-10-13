// Gezichtsafdrukken maken in de browser van de gsm, zoals de deurtablet het doet (afspraak mfn112-arcface5-v1):
// 5 punten, gelijkvormigheidstransformatie naar het ArcFace sjabloon 112x112, bilineair, RGB, (p - 127.5) / 128,
// MobileFaceNet (zelfde tflite gewichten als de tablet) en 192 waarden L2 genormaliseerd.
// Er gaat geen beeld naar de server: enkel de versleutelde afdrukken.

import { FaceLandmarker, FilesetResolver } from "./vision_bundle.mjs";
import { maakMfn } from "./mfn.mjs";

export const VERSIE = "mfn112-arcface5-v1";
const BASIS = new URL(".", import.meta.url).href.replace(/\/$/, "");
const SJABLOON = [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]];
// MediaPipe Face Mesh: irissen 468 en 473, neusbasis (tussen de neusgaten, zoals ML Kit NOSE_BASE) 2, mondhoeken 61 en 291
export const PUNTEN = { ogen: [468, 473], neus: 2, mond: [61, 291] };

let landmarker = null, beeldLandmarker = null, mfn = null;

export async function laad(voortgang = () => {}) {
  voortgang("Modellen laden");
  const [fileset, graaf, gewichten] = await Promise.all([
    FilesetResolver.forVisionTasks(BASIS + "/wasm"),
    fetch(BASIS + "/mfn.json").then((r) => { if (!r.ok) throw new Error("model niet gevonden"); return r.json(); }),
    fetch(BASIS + "/mfn.bin").then((r) => { if (!r.ok) throw new Error("model niet gevonden"); return r.arrayBuffer(); }),
  ]);
  const opties = (modus) => ({ baseOptions: { modelAssetPath: BASIS + "/face_landmarker.task", delegate: "CPU" },
    runningMode: modus, numFaces: 2, minFaceDetectionConfidence: 0.6, minFacePresenceConfidence: 0.6, minTrackingConfidence: 0.6 });
  landmarker = await FaceLandmarker.createFromOptions(fileset, opties("VIDEO"));
  beeldLandmarker = await FaceLandmarker.createFromOptions(fileset, opties("IMAGE"));
  mfn = maakMfn(graaf, gewichten);
  voortgang("Klaar");
}

// 5 punten in pixels, links en rechts volgens het beeld (niet gespiegeld)
export function vijfPunten(lm, w, h) {
  const p = (i) => [lm[i].x * w, lm[i].y * h];
  const ogen = PUNTEN.ogen.map(p).sort((a, b) => a[0] - b[0]);
  const mond = PUNTEN.mond.map(p).sort((a, b) => a[0] - b[0]);
  return [ogen[0], ogen[1], p(PUNTEN.neus), mond[0], mond[1]];
}

// hoofdhouding in graden uit de 3D punten van MediaPipe: draai (links/rechts) via de buitenste ooghoeken (33, 263),
// kanteling (op/neer) via voorhoofd (10) en kin (152). Recht in de camera kijken geeft ongeveer 0.
export function hoek(lm, w, h) {
  const p = (i) => [lm[i].x * w, lm[i].y * h, lm[i].z * w];
  const a = p(33), b = p(263), boven = p(10), onder = p(152);
  const draai = Math.atan2(b[2] - a[2], b[0] - a[0]) * 180 / Math.PI;
  const kantel = Math.atan2(onder[2] - boven[2], onder[1] - boven[1]) * 180 / Math.PI;
  return { draai, kantel };
}

// oude 2D benadering (enkel nog voor de oogafstand)
export function houding(pt) {
  const [ol, or, n, ml, mr] = pt;
  const om = [(ol[0] + or[0]) / 2, (ol[1] + or[1]) / 2], mm = [(ml[0] + mr[0]) / 2, (ml[1] + mr[1]) / 2];
  const oog = Math.hypot(or[0] - ol[0], or[1] - ol[1]);
  const as = [mm[0] - om[0], mm[1] - om[1]], lengte = Math.hypot(as[0], as[1]) || 1;
  // projectie van de neus op de as ogen-mond (kanteling) en loodrecht erop (draaiing)
  const v = [n[0] - om[0], n[1] - om[1]];
  const langs = (v[0] * as[0] + v[1] * as[1]) / (lengte * lengte);
  const dwars = (v[0] * as[1] - v[1] * as[0]) / (lengte * oog);
  return { draai: dwars, kantel: langs, oog };
}

// kleinste kwadraten gelijkvormigheid (Umeyama), van bron naar sjabloon: [a, -b, tx; b, a, ty]
export function gelijkvormig(bron, doel = SJABLOON) {
  const n = bron.length;
  let sx = 0, sy = 0, dx = 0, dy = 0;
  for (let i = 0; i < n; i++) { sx += bron[i][0]; sy += bron[i][1]; dx += doel[i][0]; dy += doel[i][1]; }
  sx /= n; sy /= n; dx /= n; dy /= n;
  let sxx = 0, a = 0, b = 0;
  for (let i = 0; i < n; i++) {
    const x = bron[i][0] - sx, y = bron[i][1] - sy, u = doel[i][0] - dx, v = doel[i][1] - dy;
    sxx += x * x + y * y; a += x * u + y * v; b += x * v - y * u;
  }
  a /= sxx; b /= sxx;
  return [a, -b, dx - (a * sx - b * sy), b, a, dy - (b * sx + a * sy)];
}

// 112x112 uitsnede, bilineair, als invoer voor het model (Float32Array RGB, (p - 127.5) / 128)
export function uitlijnen(beeld, w, h, punten) {
  const [a, mb, tx, b, , ty] = gelijkvormig(punten);
  // omgekeerde afbeelding: doelpixel -> bronpixel
  const det = a * a + b * b;
  const ia = a / det, ib = b / det;
  const uit = new Float32Array(112 * 112 * 3);
  const px = beeld.data;
  for (let y = 0; y < 112; y++) for (let x = 0; x < 112; x++) {
    const u = x - tx, v = y - ty;
    const bx = ia * u + ib * v, by = -ib * u + ia * v;
    const x0 = Math.floor(bx), y0 = Math.floor(by), fx = bx - x0, fy = by - y0;
    const o = (y * 112 + x) * 3;
    for (let c = 0; c < 3; c++) {
      const g = (xx, yy) => (xx < 0 || yy < 0 || xx >= w || yy >= h) ? 0 : px[(yy * w + xx) * 4 + c];
      const val = (1 - fy) * ((1 - fx) * g(x0, y0) + fx * g(x0 + 1, y0)) + fy * ((1 - fx) * g(x0, y0 + 1) + fx * g(x0 + 1, y0 + 1));
      uit[o + c] = (val - 127.5) / 128;
    }
  }
  void mb;
  return uit;
}

export function afdruk(invoer) { return mfn(invoer); }

export function cosinus(a, b) { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; }

// scherpte van de uitsnede (variantie van de Laplaciaan op grijs), om bewogen beelden te weigeren
export function scherpte(invoer) {
  const g = new Float32Array(112 * 112);
  for (let i = 0; i < g.length; i++) g[i] = (invoer[i * 3] + invoer[i * 3 + 1] + invoer[i * 3 + 2]) * 42.67 + 127.5;
  let s = 0, s2 = 0, n = 0;
  for (let y = 1; y < 111; y++) for (let x = 1; x < 111; x++) {
    const i = y * 112 + x, l = g[i - 1] + g[i + 1] + g[i - 112] + g[i + 112] - 4 * g[i];
    s += l; s2 += l * l; n++;
  }
  return s2 / n - (s / n) ** 2;
}
export function helderheid(invoer) {
  let s = 0; for (let i = 0; i < invoer.length; i++) s += invoer[i];
  return (s / invoer.length) * 128 + 127.5;
}

export function zoekVideo(video, tijd) {
  const r = landmarker.detectForVideo(video, tijd);
  return r.faceLandmarks || [];
}
export function zoekBeeld(beeld) {
  const r = beeldLandmarker.detect(beeld);
  return r.faceLandmarks || [];
}

function b64(buf) {
  const u = new Uint8Array(buf); let s = "";
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s);
}
function vanB64(s) { const t = atob(s); const u = new Uint8Array(t.length); for (let i = 0; i < t.length; i++) u[i] = t.charCodeAt(i); return u; }

// AES-256-GCM met aad, sleutel per tablet verpakt met RSA-OAEP (SHA-1, MGF1 SHA-1)
export async function versleutel(afdrukken, gezichtInfo) {
  if (!window.crypto || !crypto.subtle) throw new Error("Versleutelen lukt enkel via een beveiligde verbinding (https).");
  const klaar = new TextEncoder().encode(JSON.stringify({
    versie: VERSIE, bron: "gsm", punten: "mediapipe-mesh 468 473 2 61 291",
    afdrukken: afdrukken.map((a) => { const f = new Float32Array(a); const dv = new DataView(new ArrayBuffer(f.length * 4));
      f.forEach((v, i) => dv.setFloat32(i * 4, v, true)); return b64(dv.buffer); }),
  }));
  const sleutel = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(gezichtInfo.aad), tagLength: 128 }, sleutel, klaar);
  const ruw = await crypto.subtle.exportKey("raw", sleutel);
  const sleutels = {};
  for (const t of gezichtInfo.tablets) {
    const pub = await crypto.subtle.importKey("spki", vanB64(t.pubkey), { name: "RSA-OAEP", hash: "SHA-1" }, false, ["encrypt"]);
    sleutels[t.pubkey_id] = b64(await crypto.subtle.encrypt({ name: "RSA-OAEP" }, pub, ruw));
  }
  return { versie: VERSIE, aantal: afdrukken.length, iv: b64(iv), data: b64(data), sleutels,
    toestemming: gezichtInfo.toestemming.versie, toestemming_hash: gezichtInfo.toestemming.hash };
}
