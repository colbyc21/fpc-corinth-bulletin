/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{njk,md,html,js}"],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Brand palette — matches fpccorinth.org
        'sage': '#6d8c83',        // primary brand green (fills, buttons, footer)
        'sage-deep': '#56736a',   // sage for small text (AA contrast on light bg)
        'sage-light': '#eef2ef',  // sage tint for surfaces
        'ink': '#171200',         // body text
        'ink-muted': '#666558',   // secondary text
        'gold': '#847539',        // olive-gold action accent
        'khaki': '#b0af9b',       // rules / borders
        'cream': '#fdfdfb',       // page background
        'cream-dark': '#eceeea',  // card background
      },
      fontFamily: {
        'serif': ['EB Garamond', 'Georgia', 'Cambria', 'Times New Roman', 'serif'],
        'sans': ['Work Sans', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      fontSize: {
        'body': '1rem',      // 16px minimum
        'body-lg': '1.125rem', // 18px for follow-along mode
      },
      lineHeight: {
        'relaxed': '1.75',
      },
    },
  },
  plugins: [],
}
