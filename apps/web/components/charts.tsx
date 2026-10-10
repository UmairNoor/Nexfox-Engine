"use client";
import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type ISeriesApi, type UTCTimestamp, LineStyle } from "lightweight-charts";

export type Bar = { time: number; open: number; high: number; low: number; close: number };
export type Level = { price: number; color: string; title: string };

/** Live XAUUSD candles (TradingView Lightweight Charts). Pass new bars as they arrive; SL/TP/entry lines via `levels`. */
export function CandleChart({ bars, levels = [], height = 300 }: { bars: Bar[]; levels?: Level[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<any[]>([]);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      height, localization: { locale: "en-US" }, layout: { background: { color: "transparent" }, textColor: "#8B96A9", fontFamily: "JetBrains Mono, monospace", fontSize: 10 },
      grid: { vertLines: { color: "#161E2B" }, horzLines: { color: "#161E2B" } }, rightPriceScale: { borderColor: "#222C3B" },
      timeScale: { borderColor: "#222C3B", timeVisible: true, secondsVisible: false }, crosshair: { mode: 1 },
    });
    series.current = c.addCandlestickSeries({ upColor: "#22C55E", downColor: "#EF4444", borderVisible: false, wickUpColor: "#22C55E", wickDownColor: "#EF4444" });
    chart.current = c;
    const ro = new ResizeObserver(() => el.current && c.applyOptions({ width: el.current.clientWidth }));
    ro.observe(el.current);
    return () => { ro.disconnect(); c.remove(); };
  }, [height]);

  useEffect(() => {
    if (!series.current) return;
    series.current.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
  }, [bars]);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    lines.current.forEach((l) => s.removePriceLine(l));
    lines.current = levels.map((l) => s.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.title }));
  }, [levels]);

  return <div ref={el} style={{ width: "100%", height }} />;
}

/** Equity curve with area fill and an emphasised last point. */
export function EquityChart({ points, height = 220 }: { points: { t: string; equity: number }[]; height?: number }) {
  if (points.length < 2) return <div className="mu grid place-items-center" style={{ height }}>Equity curve appears after the first closed trades.</div>;
  const w = 620, L = 48, B = 20, vals = points.map((p) => p.equity);
  const mx = Math.max(...vals), mn = Math.min(...vals), pad = (mx - mn || 1) * 0.08;
  const X = (i: number) => L + (i / (points.length - 1)) * (w - L - 8);
  const Y = (v: number) => 6 + ((mx + pad - v) / (mx - mn + 2 * pad)) * (height - B - 10);
  const d = "M" + points.map((p, i) => `${X(i).toFixed(1)} ${Y(p.equity).toFixed(1)}`).join(" L");
  const ticks = [0, 1, 2, 3].map((k) => mn - pad + ((mx - mn + 2 * pad) * k) / 3);
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="block h-auto w-full" role="img" aria-label="Equity curve">
      <defs><linearGradient id="eqg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#E7B54A" stopOpacity=".3" /><stop offset="1" stopColor="#E7B54A" stopOpacity="0" /></linearGradient></defs>
      {ticks.map((t) => <g key={t}><line x1={L} x2={w} y1={Y(t)} y2={Y(t)} stroke="#1A2230" /><text x={0} y={Y(t) + 4} fontSize="10" fill="#8B96A9">{t.toLocaleString("en-US", { maximumFractionDigits: 0 })}</text></g>)}
      <path d={`${d} L${X(points.length - 1)} ${height - B} L${X(0)} ${height - B}Z`} fill="url(#eqg)" />
      <path d={d} fill="none" stroke="#E7B54A" strokeWidth="2" />
      <circle cx={X(points.length - 1)} cy={Y(vals[vals.length - 1])} r="3.5" fill="#E7B54A" />
      <text x={L} y={height - 4} fontSize="10" fill="#8B96A9">{new Date(points[0].t).toLocaleDateString("en-US")}</text>
      <text x={w - 8} y={height - 4} fontSize="10" fill="#8B96A9" textAnchor="end">{new Date(points[points.length - 1].t).toLocaleDateString("en-US")}</text>
    </svg>
  );
}

/** Signed bars around a zero line (P&L by hour, MRR). */
export function Bars({ values, labels, height = 220, color = "#22C55E" }: { values: number[]; labels: string[]; height?: number; color?: string }) {
  const w = 420, mx = Math.max(0.01, ...values), mn = Math.min(0, ...values), sc = (height - 24) / (mx - mn || 1), z = 6 + mx * sc, bw = (w - 4) / values.length;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="block h-auto w-full" role="img" aria-label="Bar chart">
      <line x1={0} x2={w} y1={z} y2={z} stroke="#222C3B" />
      {values.map((v, i) => {
        const bh = Math.abs(v) * sc;
        return <g key={i}><rect x={2 + i * bw + bw * 0.18} y={v >= 0 ? z - bh : z} width={bw * 0.64} height={Math.max(1, bh)} rx="2" fill={v >= 0 ? color : "#EF4444"}><title>{`${labels[i] || i}: ${v.toFixed(2)}`}</title></rect>
          {labels[i] && <text x={2 + i * bw + bw / 2} y={height - 3} fontSize="9.5" fill="#8B96A9" textAnchor="middle">{labels[i]}</text>}</g>;
      })}
    </svg>
  );
}
