const fs = require("fs");
const path = require("path");

// Pages that do not get the page-view counter: the front page only forwards to the
// latest bulletin (which counts itself), and a 404 is not a page anyone meant to open.
const STATS_SKIP = new Set(["index.html", "404.html"]);

function htmlFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return htmlFiles(full);
    return entry.name.endsWith(".html") ? [full] : [];
  });
}

module.exports = function(eleventyConfig) {
  // Page-view counter: once the site is written, put src/_includes/stats-beacon.js at the
  // end of every page. Done here, on the output, because the scroll bulletins are copied
  // through as finished HTML and so never pass through a layout. It is inline (not a file
  // under /js/) because the service worker serves scripts cache-first and would pin an old copy.
  eleventyConfig.on("eleventy.after", ({ dir }) => {
    const out = dir.output;
    if (!fs.existsSync(out)) return;
    const beacon = fs.readFileSync(path.join(__dirname, "src/_includes/stats-beacon.js"), "utf8");
    const tag = `<script data-fpc-stats>\n${beacon}</script>\n`;
    for (const file of htmlFiles(out)) {
      if (STATS_SKIP.has(path.relative(out, file)) || file.includes(`${path.sep}admin${path.sep}`)) continue;
      const html = fs.readFileSync(file, "utf8");
      const at = html.lastIndexOf("</body>");
      if (at === -1 || html.includes("data-fpc-stats")) continue;
      fs.writeFileSync(file, html.slice(0, at) + tag + html.slice(at));
    }
  });

  // Pass through static files
  eleventyConfig.addPassthroughCopy("src/css/style.css");
  eleventyConfig.addPassthroughCopy("src/images");
  eleventyConfig.addPassthroughCopy("admin");
  eleventyConfig.addPassthroughCopy("src/js");
  eleventyConfig.addPassthroughCopy({ "src/sw.js": "sw.js" });
  eleventyConfig.addPassthroughCopy({ "src/manifest.json": "manifest.json" });
  eleventyConfig.addPassthroughCopy({ "src/Be Blessed.mp3": "audio/be-blessed.mp3" });
  eleventyConfig.addPassthroughCopy({ "logo.png": "images/logo.png" });
  eleventyConfig.addPassthroughCopy({ "qrcode.png": "images/qrcode.png" });
  eleventyConfig.addPassthroughCopy("src/scroll");
  eleventyConfig.addPassthroughCopy({ "src/calendar.ics": "calendar.ics" });

  // Create a collection of all bulletins sorted by date (newest first)
  eleventyConfig.addCollection("bulletins", function(collectionApi) {
    return collectionApi.getFilteredByGlob("src/bulletins/*.md").sort((a, b) => {
      return new Date(b.data.date) - new Date(a.data.date);
    });
  });

  // Filter to get the latest bulletin
  eleventyConfig.addFilter("getLatest", function(collection) {
    return collection[0];
  });

  // Format date for display (accepts Date objects or YYYY-MM-DD strings)
  eleventyConfig.addFilter("formatDate", function(date) {
    const d = (typeof date === 'string') ? new Date(date + 'T12:00:00') : new Date(date);
    return d.toLocaleDateString('en-US', {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    });
  });

  // Format date for URL
  eleventyConfig.addFilter("dateToPath", function(date) {
    return new Date(date).toISOString().split('T')[0];
  });

  return {
    dir: {
      input: "src",
      output: "_site",
      includes: "_includes",
      layouts: "_includes/layouts"
    },
    templateFormats: ["njk", "md", "html"],
    htmlTemplateEngine: "njk",
    markdownTemplateEngine: "njk"
  };
};
