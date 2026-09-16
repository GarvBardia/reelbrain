import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // The graph sphere's own palette (graph-sphere/sphere-core.ts), used
        // site-wide as a RESTRAINED accent so the rest of the product reads
        // as the same thing as the graph. Rules of use: purple is the
        // working accent (focus, active nav, hover text, faint tag tints);
        // pink and blue appear only together with purple, as the gradient
        // in the hero headline and the odd small icon. Never body text,
        // never backgrounds, never turns a neutral button into a coloured
        // block. If in doubt, use less.
        sphere: {
          pink: "#e8118a",
          purple: "#7c16e8",
          blue: "#1b45e0",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        // Manrope via next/font in layout.tsx, with a system stack behind it
        // so first paint never falls back to Times.
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      keyframes: {
        // Aceternity-style slow spotlight drift for the hero.
        spotlight: {
          "0%": { opacity: "0", transform: "translate(-72%, -62%) scale(0.5)" },
          "100%": { opacity: "1", transform: "translate(-50%, -40%) scale(1)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
      },
      animation: {
        spotlight: "spotlight 2.4s ease .2s 1 forwards",
        // 1.6s, not 6s -- this is a loading cue, not ambient decoration; the
        // only consumer (Skeleton) needs it to read as "actively working" at
        // a glance, which a 6s sweep is too slow to do.
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
