// Page-view counter. .eleventy.js puts this, inline, at the end of every built page.
// It tells the bulletin builder which page was opened and which site linked to it; no
// cookie, nothing stored in the browser. The builder (builder/pipeline/stats.py) keeps the count.
(function () {
  if (location.hostname !== 'bulletin.fpccorinth.org') return; // not from previews or a local build
  try {
    var url = 'https://bulletin-admin.fpccorinth.org/hit';
    var body = JSON.stringify({ p: location.pathname, r: document.referrer });
    if (!(navigator.sendBeacon && navigator.sendBeacon(url, body))) {
      fetch(url, { method: 'POST', body: body, mode: 'no-cors', credentials: 'include', keepalive: true }).catch(function () {});
    }
  } catch (e) {}
})();
