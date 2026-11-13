// Tailwind v3 build config (scripts/build-tailwind.sh). Keep default theme/darkMode so the
// output matches the former Play CDN runtime. Rebuild app/static/css/tailwind.css whenever
// classes change in these sources.
module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./app/static/js/**/*.js",
    "./app/**/*.py",
  ],
};
