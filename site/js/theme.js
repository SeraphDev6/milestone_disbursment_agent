// Theme picker: the same three themes and the same localStorage key as the app ("mde.theme").
// "system" removes the attribute so the OS setting (and prefers-contrast) decides.
(function () {
  var select = document.getElementById("theme");
  if (!select) return;
  var KEY = "mde.theme";
  var saved = null;
  try {
    saved = localStorage.getItem(KEY);
  } catch (e) {}
  select.value = saved === "paper" || saved === "carbon" || saved === "contrast" ? saved : "system";
  select.addEventListener("change", function () {
    var v = select.value;
    if (v === "system") {
      delete document.documentElement.dataset.theme;
    } else {
      document.documentElement.dataset.theme = v;
    }
    try {
      if (v === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, v);
    } catch (e) {}
  });
})();
