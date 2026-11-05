// Applied before first paint: remembered theme override and Descriptions switch.
(function () {
  try {
    var t = localStorage.getItem("rmjf-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
    if (localStorage.getItem("rmjf-desc") === "off") document.documentElement.classList.add("no-desc");
  } catch (e) { /* storage unavailable: follow system */ }
})();
