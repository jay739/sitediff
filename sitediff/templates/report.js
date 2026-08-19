// sitediff report behaviour. Inlined by report.py so a report stays a single
// self-contained file with no network access of any kind.
(function () {
  "use strict";

  var root = document.documentElement;

  // ---- theme ----------------------------------------------------------------
  var KEY = "sitediff-theme";
  try {
    var saved = localStorage.getItem(KEY);
    if (saved) root.setAttribute("data-theme", saved);
  } catch (e) { /* private mode: fall back to the media query */ }

  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var isDark = root.getAttribute("data-theme") === "dark" ||
        (!root.hasAttribute("data-theme") &&
          window.matchMedia("(prefers-color-scheme: dark)").matches);
      var next = isDark ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
      toggle.textContent = next === "dark" ? "Light" : "Dark";
    });
  }

  // ---- filtering ------------------------------------------------------------
  var rows = Array.prototype.slice.call(document.querySelectorAll("[data-op]"));
  var chips = Array.prototype.slice.call(document.querySelectorAll(".chip[data-filter]"));
  var search = document.getElementById("search");
  var counter = document.getElementById("visible-count");
  var active = "all";

  function textOf(row) {
    if (row.dataset.text === undefined) {
      row.dataset.text = (row.textContent || "").toLowerCase();
    }
    return row.dataset.text;
  }

  function highlight(row, term) {
    var cells = row.querySelectorAll("td.t, td.f, td.v");
    for (var i = 0; i < cells.length; i++) {
      var cell = cells[i];
      if (cell.dataset.raw === undefined) cell.dataset.raw = cell.textContent;
      var raw = cell.dataset.raw;
      if (!term) { cell.textContent = raw; continue; }
      var at = raw.toLowerCase().indexOf(term);
      if (at < 0) { cell.textContent = raw; continue; }
      cell.textContent = "";
      cell.appendChild(document.createTextNode(raw.slice(0, at)));
      var mark = document.createElement("mark");
      mark.textContent = raw.slice(at, at + term.length);
      cell.appendChild(mark);
      cell.appendChild(document.createTextNode(raw.slice(at + term.length)));
    }
  }

  function apply() {
    var term = (search && search.value || "").trim().toLowerCase();
    var shown = 0;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var op = row.getAttribute("data-op");
      // Context lines are scaffolding: they follow whatever is being shown
      // rather than being filtered in their own right.
      var passesFilter = active === "all" || op === active ||
        (active !== "all" && op === "equal" && !term);
      var passesSearch = !term || textOf(row).indexOf(term) >= 0;
      var visible = passesFilter && passesSearch;
      row.classList.toggle("hidden", !visible);
      if (visible && op !== "equal") shown++;
      if (visible) highlight(row, term);
    }
    if (counter) {
      counter.textContent = shown + (shown === 1 ? " finding" : " findings");
    }
    // A section whose every finding is filtered out is noise; fold it away.
    var sections = document.querySelectorAll(".hunk");
    for (var s = 0; s < sections.length; s++) {
      var any = sections[s].querySelector("[data-op]:not(.hidden):not([data-op='equal'])");
      sections[s].style.display = any ? "" : "none";
    }
  }

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      active = chip.getAttribute("data-filter");
      chips.forEach(function (c) {
        c.setAttribute("aria-pressed", String(c === chip));
      });
      apply();
    });
  });
  if (search) search.addEventListener("input", apply);

  // ---- expand / collapse ----------------------------------------------------
  var expand = document.getElementById("expand-all");
  if (expand) {
    expand.addEventListener("click", function () {
      var sections = document.querySelectorAll(".hunk");
      var anyClosed = false;
      for (var i = 0; i < sections.length; i++) {
        if (!sections[i].open) { anyClosed = true; break; }
      }
      for (var j = 0; j < sections.length; j++) sections[j].open = anyClosed;
      expand.textContent = anyClosed ? "Collapse all" : "Expand all";
    });
  }

  // ---- sortable tables ------------------------------------------------------
  document.querySelectorAll("table[data-sortable] thead th").forEach(function (th, index) {
    th.addEventListener("click", function () {
      var table = th.closest("table");
      var body = table.tBodies[0];
      var items = Array.prototype.slice.call(body.rows);
      var asc = th.dataset.dir !== "asc";
      table.querySelectorAll("thead th").forEach(function (o) {
        delete o.dataset.dir;
        var m = o.querySelector(".dir");
        if (m) m.textContent = "";
      });
      th.dataset.dir = asc ? "asc" : "desc";
      var marker = th.querySelector(".dir");
      if (marker) marker.textContent = asc ? "▲" : "▼";

      items.sort(function (a, b) {
        var x = (a.cells[index] || {}).textContent || "";
        var y = (b.cells[index] || {}).textContent || "";
        var nx = parseFloat(x), ny = parseFloat(y);
        if (!isNaN(nx) && !isNaN(ny)) return asc ? nx - ny : ny - nx;
        return asc ? x.localeCompare(y) : y.localeCompare(x);
      });
      items.forEach(function (row) { body.appendChild(row); });
    });
  });

  // ---- keyboard -------------------------------------------------------------
  document.addEventListener("keydown", function (event) {
    if (event.target.tagName === "INPUT" || event.metaKey || event.ctrlKey) return;
    if (event.key === "/") { event.preventDefault(); if (search) search.focus(); return; }
    if (event.key === "Escape" && search) { search.value = ""; apply(); search.blur(); }
  });

  apply();
})();
