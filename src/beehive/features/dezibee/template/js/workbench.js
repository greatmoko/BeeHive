// DeziBee 分模块入口；旧需求保留原 index.html，沿用此入口加载。
const workbenchRoot = new URL(".", document.currentScript.src);
for (const name of ["navigation.js", "canvas.js", "cards.js", "prd.js", "prd_editor.js", "interactions.js", "bootstrap.js"]) {
  document.write('<script src="' + new URL(name, workbenchRoot).href + '"><\/script>');
}
