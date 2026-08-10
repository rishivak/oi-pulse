import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Trading terminal colour palette
        terminal: {
          bg: "#0d1117",
          surface: "#161b22",
          border: "#30363d",
          muted: "#8b949e",
          text: "#e6edf3",
        },
        bull: {
          DEFAULT: "#22c55e",
          dim: "#166534",
        },
        bear: {
          DEFAULT: "#ef4444",
          dim: "#7f1d1d",
        },
        neutral: {
          DEFAULT: "#94a3b8",
        },
        accent: {
          DEFAULT: "#3b82f6",
          dim: "#1e3a5f",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        sans: ["Inter", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
