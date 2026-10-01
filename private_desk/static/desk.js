// Private Desk - table sorting / chip filtering (read-only UI helpers, no network writes).
(function () {
  function cellValue(td) {
    // numeric cells carry data-v (raw number, "" when unavailable); others sort as text
    var v = td.getAttribute("data-v");
    if (v !== null) {
      if (v === "" || v === "None") return null;
      var n = parseFloat(v);
      return isNaN(n) ? v.toLowerCase() : n;
    }
    v = td.textContent.trim();
    return (v === "" || v === "UNAVAILABLE") ? null : v.toLowerCase();
  }
  document.querySelectorAll("table.sortable").forEach(function (table) {
    table.querySelectorAll("th.sort").forEach(function (th, _) {
      th.addEventListener("click", function () {
        var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
        var asc = !th.classList.contains("asc");
        table.querySelectorAll("th").forEach(function (h) { h.classList.remove("asc", "desc"); });
        th.classList.add(asc ? "asc" : "desc");
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = cellValue(a.cells[idx]), y = cellValue(b.cells[idx]);
          if (x === null && y === null) return 0;
          if (x === null) return 1;            // unavailable always last
          if (y === null) return -1;
          if (x < y) return asc ? -1 : 1;
          if (x > y) return asc ? 1 : -1;
          return 0;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });

  // chip filters: each chip toggles a token; a row shows when it carries every active token
  document.querySelectorAll(".filters[data-table]").forEach(function (bar) {
    var table = document.getElementById(bar.getAttribute("data-table"));
    var search = bar.querySelector("input[data-search]");
    var sector = bar.querySelector("select[data-sector]");
    var count = bar.querySelector("[data-count]");
    function apply() {
      var active = Array.prototype.map.call(bar.querySelectorAll(".chip.on"), function (c) { return c.getAttribute("data-tok"); });
      var q = search ? search.value.trim().toUpperCase() : "";
      var sec = sector ? sector.value : "";
      var shown = 0;
      Array.prototype.forEach.call(table.tBodies[0].rows, function (r) {
        var toks = (" " + (r.getAttribute("data-tokens") || "") + " ");
        var ok = active.every(function (t) { return toks.indexOf(" " + t + " ") >= 0; });
        if (q && (r.getAttribute("data-symbol") || "").indexOf(q) < 0 &&
            (r.getAttribute("data-company") || "").toUpperCase().indexOf(q) < 0) ok = false;
        if (sec && r.getAttribute("data-sector") !== sec) ok = false;
        r.style.display = ok ? "" : "none";
        if (ok) shown++;
      });
      if (count) count.textContent = shown + " shown";
    }
    bar.querySelectorAll(".chip").forEach(function (c) {
      c.addEventListener("click", function () { c.classList.toggle("on"); apply(); });
    });
    if (search) search.addEventListener("input", apply);
    if (sector) sector.addEventListener("change", apply);
    apply();
  });
})();
