// MobileFaceNet in gewone JavaScript, zonder externe runtime.
// Voert de graaf uit die export_mfn.py uit mobilefacenet.tflite haalde (zelfde gewichten, float32, NHWC).
// Invoer: Float32Array 112*112*3 (RGB, (pixel - 127.5) / 128). Uitvoer: Float32Array 192, L2 genormaliseerd.

const SAME = 0;

function conv2d(x, xs, w, ws, b, o) {
  const [, H, W, C] = xs, [O, KH, KW, I] = ws;
  if (I !== C) throw new Error("conv: kanalen kloppen niet");
  const sh = o.sh, sw = o.sw;
  let OH, OW, pt = 0, pl = 0;
  if (o.pad === SAME) {
    OH = Math.ceil(H / sh); OW = Math.ceil(W / sw);
    pt = Math.floor(Math.max((OH - 1) * sh + KH - H, 0) / 2);
    pl = Math.floor(Math.max((OW - 1) * sw + KW - W, 0) / 2);
  } else { OH = Math.floor((H - KH) / sh) + 1; OW = Math.floor((W - KW) / sw) + 1; }
  const y = new Float32Array(OH * OW * O);
  if (KH === 1 && KW === 1 && sh === 1 && sw === 1) {
    // 1x1: het meeste rekenwerk; per pixel een matrix x vector
    for (let p = 0; p < OH * OW; p++) {
      const xo = p * C, yo = p * O;
      for (let oc = 0; oc < O; oc++) {
        let s = b ? b[oc] : 0; const wo = oc * C;
        for (let ic = 0; ic < C; ic++) s += x[xo + ic] * w[wo + ic];
        y[yo + oc] = s;
      }
    }
    return [y, [1, OH, OW, O]];
  }
  for (let oy = 0; oy < OH; oy++) for (let ox = 0; ox < OW; ox++) {
    const yo = (oy * OW + ox) * O;
    for (let oc = 0; oc < O; oc++) {
      let s = b ? b[oc] : 0;
      for (let ky = 0; ky < KH; ky++) {
        const iy = oy * sh - pt + ky * (o.dh || 1); if (iy < 0 || iy >= H) continue;
        for (let kx = 0; kx < KW; kx++) {
          const ix = ox * sw - pl + kx * (o.dw || 1); if (ix < 0 || ix >= W) continue;
          const xo = (iy * W + ix) * C, wo = ((oc * KH + ky) * KW + kx) * C;
          for (let ic = 0; ic < C; ic++) s += x[xo + ic] * w[wo + ic];
        }
      }
      y[yo + oc] = s;
    }
  }
  return [y, [1, OH, OW, O]];
}

function depthwise(x, xs, w, ws, b, o) {
  const [, H, W, C] = xs, [, KH, KW, CM] = ws;
  const mult = o.mult || 1; if (CM !== C * mult) throw new Error("depthwise: kanalen kloppen niet");
  const sh = o.sh, sw = o.sw;
  let OH, OW, pt = 0, pl = 0;
  if (o.pad === SAME) {
    OH = Math.ceil(H / sh); OW = Math.ceil(W / sw);
    pt = Math.floor(Math.max((OH - 1) * sh + KH - H, 0) / 2);
    pl = Math.floor(Math.max((OW - 1) * sw + KW - W, 0) / 2);
  } else { OH = Math.floor((H - KH) / sh) + 1; OW = Math.floor((W - KW) / sw) + 1; }
  const y = new Float32Array(OH * OW * CM);
  for (let oy = 0; oy < OH; oy++) for (let ox = 0; ox < OW; ox++) {
    const yo = (oy * OW + ox) * CM;
    for (let c = 0; c < CM; c++) y[yo + c] = b ? b[c] : 0;
    for (let ky = 0; ky < KH; ky++) {
      const iy = oy * sh - pt + ky * (o.dh || 1); if (iy < 0 || iy >= H) continue;
      for (let kx = 0; kx < KW; kx++) {
        const ix = ox * sw - pl + kx * (o.dw || 1); if (ix < 0 || ix >= W) continue;
        const xo = (iy * W + ix) * C, wo = (ky * KW + kx) * CM;
        for (let c = 0; c < CM; c++) y[yo + c] += x[xo + Math.floor(c / mult)] * w[wo + c];
      }
    }
  }
  return [y, [1, OH, OW, CM]];
}

function binair(a, as, b, bs, f) {
  // broadcast enkel over de laatste as (vector per kanaal) of een scalair; dat is alles wat dit model gebruikt
  const n = Math.max(a.length, b.length);
  if (n % a.length || n % b.length) throw new Error("broadcast niet ondersteund");
  const y = new Float32Array(n), la = a.length, lb = b.length;
  if (la === n && lb === n) for (let i = 0; i < n; i++) y[i] = f(a[i], b[i]);
  else for (let i = 0; i < n; i++) y[i] = f(a[i % la], b[i % lb]);
  return [y, (a.length >= b.length ? as : bs)];
}

export function maakMfn(graaf, gewichten) {
  const w = new Float32Array(gewichten);
  const cnst = {};
  for (const [k, t] of Object.entries(graaf.tensors)) if (t.w) cnst[k] = w.subarray(t.w[0], t.w[0] + t.w[1]);
  const vorm = (k) => graaf.tensors[k].shape;
  return function run(invoer) {
    if (invoer.length !== 112 * 112 * 3) throw new Error("invoer moet 112x112x3 zijn");
    const val = { [graaf.input]: [invoer, [1, 112, 112, 3]] };
    const get = (k) => val[k] || [cnst[k], vorm(k)];
    for (const op of graaf.ops) {
      const [i0, i1, i2] = op.in; let r;
      switch (op.op) {
        case "CONV_2D": { const [x, xs] = get(i0); r = conv2d(x, xs, cnst[i1], vorm(i1), i2 >= 0 ? cnst[i2] : null, op.o); break; }
        case "DEPTHWISE_CONV_2D": { const [x, xs] = get(i0); r = depthwise(x, xs, cnst[i1], vorm(i1), i2 >= 0 ? cnst[i2] : null, op.o); break; }
        case "RELU": { const [x, xs] = get(i0); const y = new Float32Array(x.length); for (let i = 0; i < x.length; i++) y[i] = x[i] > 0 ? x[i] : 0; r = [y, xs]; break; }
        case "ABS": { const [x, xs] = get(i0); const y = new Float32Array(x.length); for (let i = 0; i < x.length; i++) y[i] = Math.abs(x[i]); r = [y, xs]; break; }
        case "ADD": { const [a, as] = get(i0), [b, bs] = get(i1); r = binair(a, as, b, bs, (p, q) => p + q); break; }
        case "SUB": { const [a, as] = get(i0), [b, bs] = get(i1); r = binair(a, as, b, bs, (p, q) => p - q); break; }
        case "MUL": { const [a, as] = get(i0), [b, bs] = get(i1); r = binair(a, as, b, bs, (p, q) => p * q); break; }
        case "RESHAPE": { const [x] = get(i0); r = [x, vorm(op.out[0])]; break; }
        case "L2_NORMALIZATION": {
          const [x, xs] = get(i0); let s = 0; for (let i = 0; i < x.length; i++) s += x[i] * x[i];
          const n = 1 / Math.sqrt(Math.max(s, 1e-12)); const y = new Float32Array(x.length);
          for (let i = 0; i < x.length; i++) y[i] = x[i] * n; r = [y, xs]; break;
        }
        default: throw new Error("onbekende bewerking " + op.op);
      }
      if (op.o && op.o.act) throw new Error("ingebouwde activatie niet ondersteund");
      val[op.out[0]] = r;
      // tussenresultaten die niet meer nodig zijn vrijgeven
    }
    return val[graaf.output][0];
  };
}
