/* AI Smart Tender: pdf.js polyfills. Полифилы для pdf.js 5.5 в Electron 37 (Chromium 138), вставляются в начало pdf.js и его воркера. */
(() => {
  const def = (o, k, v) => { if (o && !o[k]) Object.defineProperty(o, k, { value: v, writable: true, configurable: true }); };
  for (const C of [globalThis.Map, globalThis.WeakMap]) {
    def(C.prototype, "getOrInsert", function (k, v) { if (!this.has(k)) this.set(k, v); return this.get(k); });
    def(C.prototype, "getOrInsertComputed", function (k, f) { if (!this.has(k)) this.set(k, f(k)); return this.get(k); });
  }
  def(Math, "sumPrecise", function (items) {
    let s = 0, c = 0, any = false;
    for (const x of items) {
      any = true;
      const t = s + x;
      c += Math.abs(s) >= Math.abs(x) ? (s - t) + x : (x - t) + s;
      s = t;
    }
    return any ? s + c : -0;
  });
  def(Promise, "try", function (f, ...a) { return new Promise((r) => r(f(...a))); });
  const U = Uint8Array, hex = "0123456789abcdef";
  def(U.prototype, "toHex", function () { let s = ""; for (const b of this) s += hex[b >> 4] + hex[b & 15]; return s; });
  def(U, "fromHex", function (s) { const a = new U(s.length >> 1); for (let i = 0; i < a.length; i++) a[i] = parseInt(s.substr(i * 2, 2), 16); return a; });
  def(U.prototype, "toBase64", function () { let s = ""; for (let i = 0; i < this.length; i += 0x8000) s += String.fromCharCode.apply(null, this.subarray(i, i + 0x8000)); return btoa(s); });
  def(U, "fromBase64", function (s) { const b = atob(s.replace(/[\s=]+$/g, "").replace(/-/g, "+").replace(/_/g, "/")); const a = new U(b.length); for (let i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return a; });
})();
