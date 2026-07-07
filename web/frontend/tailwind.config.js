/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // background/surface/border were literally identical hex values to
        // RPMWorks' Slate palette (both just copied Slate 900/800/700) -
        // only `primary` differed, so the two apps read as near-identical
        // at a glance. Retinted the neutrals toward violet (to match
        // `primary`) instead, same lightness/darkness as before so it's
        // still an unmistakably dark theme, just a different hue family.
        background: '#120f1f', // dark violet-black (was Slate 900 #0f172a)
        surface: '#1c1830',    // dark violet-gray   (was Slate 800 #1e293b)
        'surface-hover': '#282140', // (was #293548)
        primary: '#a855f7',    // Purple 500 (indigo-500 was tried first but reads too close to RPMWorks' blue at a glance - purple is unmistakably different)
        'primary-hover': '#9333ea', // Purple 600
        secondary: '#64748b',  // Slate 500
        text: '#f1f5f9',       // Slate 100
        'text-muted': '#94a3b8', // Slate 400
        border: '#3a2f5c',     // violet-gray (was Slate 700 #334155)
      }
    },
  },
  plugins: [],
}
