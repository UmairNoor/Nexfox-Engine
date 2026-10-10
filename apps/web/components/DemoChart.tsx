"use client";
import { useEffect, useState } from "react";
import { CandleChart, type Bar } from "./charts";

/** Landing-page demo: a simulated XAUUSD feed (clearly labelled as a demo, not live results). */
export default function DemoChart() {
  const [bars, setBars] = useState<Bar[]>([]);
  useEffect(() => {
    let p = 3412, t = Math.floor(Date.now() / 60000) * 60 - 90 * 60;
    const g = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
    const init: Bar[] = [];
    for (let i = 0; i < 90; i++) { const o = p, c = o + g() * 1.3; init.push({ time: t, open: o, high: Math.max(o, c) + Math.abs(g()) * .7, low: Math.min(o, c) - Math.abs(g()) * .7, close: c }); p = c; t += 60; }
    setBars(init);
    const iv = setInterval(() => setBars((b) => {
      const last = b[b.length - 1], c = last.close + g() * .35;
      const nb = { ...last, close: c, high: Math.max(last.high, c), low: Math.min(last.low, c) };
      return Math.random() < .08 ? [...b.slice(1, -1), nb, { time: last.time + 60, open: c, high: c, low: c, close: c }] : [...b.slice(0, -1), nb];
    }), 700);
    return () => clearInterval(iv);
  }, []);
  return bars.length ? <CandleChart bars={bars} height={260} /> : <div style={{ height: 260 }} />;
}
