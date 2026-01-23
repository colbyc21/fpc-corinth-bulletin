/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{njk,md,html,js}"],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'navy': '#1a3a5c',
        'gold': '#c9a959',
        'cream': '#faf9f7',
      },
      fontFamily: {
        'serif': ['Georgia', 'Cambria', 'Times New Roman', 'serif'],
        'sans': ['system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
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
