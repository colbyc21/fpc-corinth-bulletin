module.exports = function(eleventyConfig) {
  // Pass through static files
  eleventyConfig.addPassthroughCopy("src/css/style.css");
  eleventyConfig.addPassthroughCopy("src/images");
  eleventyConfig.addPassthroughCopy("admin");
  eleventyConfig.addPassthroughCopy("src/js");
  eleventyConfig.addPassthroughCopy({ "src/sw.js": "sw.js" });
  eleventyConfig.addPassthroughCopy({ "src/manifest.json": "manifest.json" });
  eleventyConfig.addPassthroughCopy({ "src/Be Blessed.mp3": "audio/be-blessed.mp3" });

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

  // Format date for display
  eleventyConfig.addFilter("formatDate", function(date) {
    return new Date(date).toLocaleDateString('en-US', {
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
