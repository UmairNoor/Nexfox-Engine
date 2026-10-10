import type { Config } from "tailwindcss";
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  safelist: ["side-BUY", "side-SELL"],
  theme: {
    extend: {
      colors: {
        bg: "#090D14", rail: "#0C111A", surface: "#111722", surface2: "#172030", line: "#222C3B", line2: "#1A2230",
        ink: "#E7EBF1", muted: "#8B96A9", faint: "#5E687A", gold: "#E7B54A", good: "#22C55E", bad: "#EF4444", warn: "#F59E0B", info: "#60A5FA",
      },
      fontFamily: { sans: ["Manrope", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
} satisfies Config;
