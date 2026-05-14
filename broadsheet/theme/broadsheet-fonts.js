// broadsheet-fonts.js — load broadsheet's four-font editorial register
// into Home Assistant's frontend.
//
// OPTIONAL. The broadsheet HA theme (broadsheet.yaml) works without
// this — its font stacks fall back to Iowan / Georgia / system-ui /
// Menlo, which keep the serif-forward feel. This module gets you the
// exact fonts.
//
// To use: copy this file into your HA `/config/www/` directory, then
// add to configuration.yaml:
//
//   frontend:
//     extra_module_url:
//       - /local/broadsheet-fonts.js
//
// …and reload core config (or restart HA).
//
// Fonts loaded:
//   • Instrument Serif  — display / headlines (italic capable)
//   • Newsreader        — body text (variable opsz, italic capable)
//   • IBM Plex Sans     — captions / labels / buttons
//   • JetBrains Mono    — code / technical
//
// Idempotent — no-op if already injected. To remove: delete the
// extra_module_url line + reload core config; the theme falls back to
// system fonts, nothing visibly breaks.

(() => {
  if (document.head.querySelector('link[data-broadsheet-fonts]')) return;

  const preconnect1 = document.createElement('link');
  preconnect1.rel = 'preconnect';
  preconnect1.href = 'https://fonts.googleapis.com';
  document.head.appendChild(preconnect1);

  const preconnect2 = document.createElement('link');
  preconnect2.rel = 'preconnect';
  preconnect2.href = 'https://fonts.gstatic.com';
  preconnect2.crossOrigin = '';
  document.head.appendChild(preconnect2);

  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href =
    'https://fonts.googleapis.com/css2' +
    '?family=Instrument+Serif:ital@0;1' +
    '&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400' +
    '&family=IBM+Plex+Sans:ital,wght@0,400;0,500;1,400' +
    '&family=JetBrains+Mono:wght@400;500' +
    '&display=swap';
  link.dataset.broadsheetFonts = '1';
  document.head.appendChild(link);
})();
