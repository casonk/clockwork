/* The themes page: edit your portfolio themes, colours and emoji.
 *
 * Everything shown comes from a file you can edit, so the DOM is built with textContent and
 * properties, never innerHTML. The server (traction-control's portfolio_themes.py) validates the
 * result again; this script only makes mistakes hard to make. */
(function () {
  "use strict";
  var main = document.querySelector("main");
  var CSRF = main.dataset.csrf;
  var HEX = /^#[0-9a-fA-F]{6}$/;
  var SLUG = /^[a-z][a-z0-9-]{0,31}$/;
  var TOKEN_LABELS = {
    bg: "Page background", surface: "Cards and panels", surface2: "Raised rows", border: "Borders",
    text: "Text", muted: "Secondary text", accent: "Accent", "accent-dim": "Accent fill",
    "accent-ink": "Text on accent", green: "Green", "green-dim": "Green fill", red: "Red",
    "red-dim": "Red fill", yellow: "Yellow", "yellow-dim": "Yellow fill", purple: "Purple", "purple-dim": "Purple fill"
  };
  var S = null;          // the state being edited: themes (in menu order), palette, emoji, tokens
  var selected = null;   // the name of the theme in the editor
  var saved = "";        // JSON of the state as last loaded or saved, to tell what changed
  var armed = null;      // the token whose colour a click on a saved colour sets

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else if (k === "class") node.className = attrs[k];
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { node.appendChild(c); });
    return node;
  }
  var $ = function (id) { return document.getElementById(id); };

  function say(text, kind) {
    var box = $("msg");
    box.textContent = text;
    box.className = kind || "";
    box.hidden = !text;
  }

  function api(path, body) {
    var options = body === undefined
      ? { headers: { Accept: "application/json" } }
      : { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: JSON.stringify(body) };
    return fetch(path, options).then(function (r) { return r.json().then(function (data) { return { status: r.status, data: data }; }); });
  }

  function snapshot() {
    return JSON.stringify({ t: S.themes, p: S.palette, e: S.emoji });
  }

  function markDirty() {
    $("dirty").textContent = snapshot() === saved ? "" : "Unsaved changes";
  }

  function load(state) {
    S = JSON.parse(JSON.stringify(state));
    S.themes.forEach(function (t) { t.tokens = Object.assign({}, t.tokens); });
    if (!S.themes.some(function (t) { return t.name === selected; })) selected = S.themes.length ? S.themes[0].name : null;
    saved = snapshot();
    render();
  }

  function theme(name) {
    return S.themes.filter(function (t) { return t.name === name; })[0];
  }

  // ---- the theme list
  function renderList() {
    var list = $("theme-list");
    list.replaceChildren();
    S.themes.forEach(function (t, i) {
      var swatch = el("span", { class: "swatch", "aria-hidden": "true" }, ["bg", "accent", "text"].map(function (k) {
        var cell = el("i"); cell.style.background = t.tokens[k]; return cell;
      }));
      var active = el("input", { type: "checkbox", "aria-label": "Offer " + t.label });
      active.checked = !!t.active;
      active.addEventListener("click", function (ev) { ev.stopPropagation(); });
      active.addEventListener("change", function () { t.active = active.checked; markDirty(); });
      var up = el("button", { class: "small", type: "button", title: "Move up", text: "↑" });
      up.disabled = i === 0;
      up.addEventListener("click", function (ev) { ev.stopPropagation(); move(i, -1); });
      var down = el("button", { class: "small", type: "button", title: "Move down", text: "↓" });
      down.disabled = i === S.themes.length - 1;
      down.addEventListener("click", function (ev) { ev.stopPropagation(); move(i, 1); });
      var kids = [active, swatch, el("span", { class: "name", text: (t.icon || "") + " " + t.label }), el("span", { class: "badge", text: t.origin === "custom" ? "yours" : "tracked" })];
      if (t.modified) kids.push(el("span", { class: "badge changed", text: "changed" }));
      kids.push(up, down);
      var row = el("li", { class: "theme-row" + (t.name === selected ? " selected" : ""), tabindex: "0", role: "button" }, kids);
      var choose = function () { selected = t.name; render(); };
      row.addEventListener("click", choose);
      row.addEventListener("keydown", function (ev) { if (ev.key === "Enter") choose(); });
      list.appendChild(row);
    });
  }

  function move(i, by) {
    var j = i + by;
    if (j < 0 || j >= S.themes.length) return;
    var t = S.themes.splice(i, 1)[0];
    S.themes.splice(j, 0, t);
    render();
  }

  // ---- the editor
  function field(label, input) {
    return el("label", {}, [document.createTextNode(label), input]);
  }

  function textInput(t, key, max) {
    var input = el("input", { type: "text", maxlength: String(max) });
    input.value = t[key] || "";
    input.addEventListener("input", function () { t[key] = input.value; renderList(); preview(t); markDirty(); });
    return input;
  }

  function setToken(t, key, value) {
    t.tokens[key] = value.toLowerCase();
    renderList(); preview(t); markDirty();
  }

  function renderEditor() {
    var box = $("editor");
    var t = selected && theme(selected);
    box.replaceChildren();
    if (!t) { box.appendChild(el("p", { class: "muted", text: "Pick a theme to edit it." })); return; }
    if (armed && !t.tokens[armed]) armed = null;
    box.appendChild(el("h2", { text: "Edit " + t.label }));

    var scheme = el("select");
    ["dark", "light"].forEach(function (s) { var o = el("option", { value: s, text: s }); o.selected = t.scheme === s; scheme.appendChild(o); });
    scheme.addEventListener("change", function () { t.scheme = scheme.value; markDirty(); });
    var icon = textInput(t, "icon", 16);
    var emojiPick = el("div", { class: "chips" }, Object.keys(S.emoji).sort().map(function (name) {
      return el("button", { class: "small", type: "button", title: name, text: S.emoji[name], onclick: function () { t.icon = S.emoji[name]; render(); markDirty(); } });
    }));
    var stripe = textInput(t, "stripe", 200);
    stripe.placeholder = "linear-gradient(90deg, #f00 0 50%, #00f 50%) or empty";
    box.appendChild(el("div", { class: "fields" }, [
      field("Name", textInput(t, "label", 32)),
      field("Description", textInput(t, "description", 120)),
      field("Scheme", scheme),
      field("Icon", icon),
      field("Stripe under the top bar", stripe)
    ]));
    if (Object.keys(S.emoji).length) box.appendChild(el("div", { class: "muted", text: "Icon from your emoji:" }, [emojiPick]));

    box.appendChild(preview(t, true));

    var grid = el("div", { class: "tokens" });
    S.tokens.forEach(function (key) {
      var color = el("input", { type: "color", "aria-label": TOKEN_LABELS[key] || key });
      var hex = el("input", { type: "text", maxlength: "7", "aria-label": (TOKEN_LABELS[key] || key) + " hex" });
      color.value = t.tokens[key]; hex.value = t.tokens[key];
      [color, hex].forEach(function (input) { input.addEventListener("focus", function () { armed = key; }); });
      color.addEventListener("input", function () { hex.value = color.value; setToken(t, key, color.value); });
      hex.addEventListener("change", function () {
        if (HEX.test(hex.value)) { color.value = hex.value; setToken(t, key, hex.value); }
        else { hex.value = t.tokens[key]; say("Use a colour like #1f6feb.", "err"); }
      });
      grid.appendChild(el("div", { class: "token" }, [el("span", { text: TOKEN_LABELS[key] || key }), color, hex]));
    });
    box.appendChild(grid);

    var names = Object.keys(S.palette).sort();
    if (names.length) {
      box.appendChild(el("div", { class: "muted", text: "Click a token's colour, then a saved colour to use it for that token:" }));
      box.appendChild(el("div", { class: "chips" }, names.map(function (name) {
        var dot = el("span", { class: "dot" }); dot.style.background = S.palette[name];
        return el("button", { class: "small chip", type: "button", title: S.palette[name], onclick: function () {
          if (!armed) { say("Click one of the token colours above first.", "err"); return; }
          setToken(t, armed, S.palette[name]);
          renderEditor();
          say("Set " + (TOKEN_LABELS[armed] || armed) + " to " + name + " (" + S.palette[name] + ").", "ok");
        } }, [dot, document.createTextNode(name)]);
      })));
    }

    var actions = el("div", { class: "actions" });
    if (t.tracked) {
      actions.appendChild(el("button", { type: "button", text: "Revert to the tracked theme", onclick: function () {
        t.label = t.tracked.label; t.description = t.tracked.description; t.scheme = t.tracked.scheme;
        t.icon = t.tracked.icon; t.stripe = t.tracked.stripe; t.tokens = Object.assign({}, t.tracked.tokens);
        render(); markDirty();
      } }));
    }
    if (t.origin === "custom") {
      actions.appendChild(el("button", { type: "button", text: "Delete this theme", onclick: function () {
        if (!confirm("Delete " + t.label + "?")) return;
        S.themes = S.themes.filter(function (x) { return x !== t; });
        selected = S.themes.length ? S.themes[0].name : null;
        render(); markDirty();
      } }));
    }
    box.appendChild(actions);
  }

  function preview(t, create) {
    var node = create ? el("div", { class: "preview", id: "preview" }) : $("preview");
    if (!node) return null;
    Object.keys(t.tokens).forEach(function (k) { node.style.setProperty("--" + k, t.tokens[k]); });
    node.style.setProperty("--stripe", t.stripe || "transparent");
    if (create) {
      node.appendChild(el("strong", { text: (t.icon || "") + " " + t.label }));
      node.appendChild(el("div", { class: "muted", text: t.description || "Preview of this theme" }));
      var card = el("div", { class: "card" }, [
        el("div", { text: "A card with body text" }),
        el("div", { class: "muted", text: "and secondary text" }),
        el("div", {}, [el("span", { class: "acc", text: "Accent" })])
      ]);
      ["green", "red", "yellow", "purple"].forEach(function (k) {
        var chip = el("span", { class: "st", text: k });
        chip.style.background = "var(--" + k + "-dim)"; chip.style.color = "var(--" + k + ")";
        card.appendChild(chip);
      });
      node.appendChild(card);
    }
    return node;
  }

  // ---- the colour and emoji libraries
  function chips(boxId, map, draw, drop) {
    var box = $(boxId);
    box.replaceChildren();
    Object.keys(map).sort().forEach(function (name) {
      var remove = el("button", { type: "button", title: "Remove " + name, "aria-label": "Remove " + name, text: "×" });
      remove.addEventListener("click", function () { delete map[name]; drop(); render(); markDirty(); });
      box.appendChild(el("span", { class: "chip" }, [draw(name), document.createTextNode(" " + name + " "), remove]));
    });
    if (!Object.keys(map).length) box.appendChild(el("span", { class: "muted", text: "None saved yet." }));
  }

  function renderLibraries() {
    chips("palette", S.palette, function (name) { var d = el("span", { class: "dot" }); d.style.background = S.palette[name]; return d; }, function () {});
    chips("emoji", S.emoji, function (name) { return document.createTextNode(S.emoji[name]); }, function () {});
  }

  function render() {
    renderList(); renderEditor(); renderLibraries(); markDirty();
  }

  // ---- actions
  function payload() {
    return {
      themes: S.themes.map(function (t) {
        return { name: t.name, label: t.label, description: t.description, scheme: t.scheme, icon: t.icon, stripe: t.stripe, tokens: t.tokens, based_on: t.based_on || null };
      }),
      active: S.themes.filter(function (t) { return t.active; }).map(function (t) { return t.name; }),
      palette: S.palette,
      emoji: S.emoji
    };
  }

  function save() {
    say("Saving…", "");
    api("/api/themes/apply", payload()).then(function (res) {
      if (res.data.ok) {
        say("Saved. Reloading to use it…", "ok");
        setTimeout(function () { location.reload(); }, 600);
      } else {
        say(res.data.error || "Could not save.", "err");
      }
    }).catch(function () { say("Could not reach Clockwork.", "err"); });
  }

  $("save").addEventListener("click", save);
  $("revert").addEventListener("click", function () { init(); say("", ""); });
  $("reset-all").addEventListener("click", function () {
    if (!confirm("Drop all of your themes, colours and emoji and go back to the tracked themes?")) return;
    api("/api/themes/reset", {}).then(function (res) {
      if (res.data.ok) { say("Reset. Reloading…", "ok"); setTimeout(function () { location.reload(); }, 600); }
      else say(res.data.error || "Could not reset.", "err");
    });
  });
  $("pal-add").addEventListener("click", function () {
    var name = $("pal-name").value.trim();
    if (!SLUG.test(name)) { say("Name a colour with lowercase letters, digits and -, starting with a letter.", "err"); return; }
    S.palette[name] = $("pal-color").value; $("pal-name").value = ""; render(); markDirty();
  });
  $("emo-add").addEventListener("click", function () {
    var name = $("emo-name").value.trim(), glyph = $("emo-glyph").value.trim();
    if (!SLUG.test(name)) { say("Name an emoji with lowercase letters, digits and -, starting with a letter.", "err"); return; }
    if (!glyph || /^[\x00-\x7f]+$/.test(glyph)) { say("Enter an emoji or symbol, not plain text.", "err"); return; }
    S.emoji[name] = glyph; $("emo-name").value = ""; $("emo-glyph").value = ""; render(); markDirty();
  });
  $("new-theme").addEventListener("click", function () {
    var name = (prompt("A short name for the new theme (lowercase letters, digits, -):") || "").trim();
    if (!name) return;
    if (!SLUG.test(name) || theme(name)) { say("Pick an unused name of lowercase letters, digits and -.", "err"); return; }
    var from = theme(selected) || S.themes[0];
    var copy = JSON.parse(JSON.stringify(from));
    var builtin = from.origin === "builtin" ? from : (S.themes.filter(function (x) { return x.name === from.based_on; })[0] || S.themes.filter(function (x) { return x.origin === "builtin"; })[0]);
    copy.name = name; copy.label = name.charAt(0).toUpperCase() + name.slice(1); copy.origin = "custom";
    copy.modified = false; copy.active = true; copy.based_on = builtin.name; copy.tracked = null;
    S.themes.push(copy); selected = name; render(); markDirty();
  });

  function init() {
    api("/api/themes").then(function (res) {
      if (res.data.csrf_token) CSRF = res.data.csrf_token;
      if (res.data.ok) { load(res.data.state); say("", ""); }
      else say(res.data.error || "Could not load the themes.", "err");
    }).catch(function () { say("Could not reach Clockwork.", "err"); });
  }

  window.addEventListener("beforeunload", function (ev) {
    if (S && snapshot() !== saved) { ev.preventDefault(); ev.returnValue = ""; }
  });
  if (main.dataset.tool === "yes") init();
})();
