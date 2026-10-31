/* The themes page: edit your portfolio themes, colours, emoji, and the shared font and page scale.
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
  // Colours are named by what they are for, not by hue: "Purple" says nothing, "Highlight" does.
  // Each status colour sits beside the background that carries its text.
  var TOKEN_GROUPS = [
    { title: "Page and panels", tokens: [
      ["bg", "Page background", "Behind everything"],
      ["surface", "Cards and panels", "Panels, menus and the top bar"],
      ["surface2", "Raised and hovered rows", "Table headers, hovered rows, input boxes"],
      ["border", "Borders and dividers", "Lines around panels and between rows"]
    ] },
    { title: "Text", tokens: [
      ["text", "Main text", "Body text and headings"],
      ["muted", "Secondary text", "Hints, captions and labels"]
    ] },
    { title: "Accent (the main colour)", tokens: [
      ["accent", "Accent", "Primary buttons, links and the selected item"],
      ["accent-dim", "Accent background", "Selected rows and badges, behind accent text"],
      ["accent-ink", "Text on accent", "Text on a solid accent button"]
    ] },
    { title: "Status colours (each with its background)", tokens: [
      ["green", "Success (green)", "Passed, done, healthy: text and icons"],
      ["green-dim", "Success background", "Behind success text"],
      ["red", "Error (red)", "Failed, danger, delete"],
      ["red-dim", "Error background", "Behind error text"],
      ["yellow", "Warning (yellow)", "Pending, caution, needs a look"],
      ["yellow-dim", "Warning background", "Behind warning text"],
      ["purple", "Highlight (purple)", "A second chart colour and special markers"],
      ["purple-dim", "Highlight background", "Behind highlight text"]
    ] }
  ];
  var TOKEN_LABELS = {};
  TOKEN_GROUPS.forEach(function (g) { g.tokens.forEach(function (t) { TOKEN_LABELS[t[0]] = t[1]; }); });
  var S = null;          // the state being edited: themes (in menu order), palette, emoji, labels, type, tokens
  var CATALOG = [];      // what can be renamed or re-iconed, with its defaults
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
    if (text && kind === "err" && box.scrollIntoView) box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function api(path, body) {
    var options = body === undefined
      ? { headers: { Accept: "application/json" } }
      : { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: JSON.stringify(body) };
    return fetch(path, options).then(function (r) { return r.json().then(function (data) { return { status: r.status, data: data }; }); });
  }

  function snapshot() {
    return JSON.stringify({ t: S.themes, p: S.palette, e: S.emoji, l: S.labels, y: S.type });
  }

  function markDirty() {
    $("dirty").textContent = snapshot() === saved ? "" : "Unsaved changes";
  }

  function load(state, catalog) {
    CATALOG = catalog || CATALOG;
    S = JSON.parse(JSON.stringify(state));
    S.labels = S.labels || {};
    S.themes.forEach(function (t) { t.tokens = Object.assign({}, t.tokens); });
    S.type = S.type || { font: "system", size: "medium" };
    if (typeof S.type.size === "number") S.type.size = "medium";
    S.fonts = S.fonts && S.fonts.length ? S.fonts : [
      { name: "system", label: "System" }, { name: "serif", label: "Serif" }, { name: "mono", label: "Mono" }
    ];
    S.sizes = S.sizes && S.sizes.length ? S.sizes : [
      { name: "tiny", label: "Tiny" }, { name: "small", label: "Small" },
      { name: "medium", label: "Medium" }, { name: "large", label: "Large" }
    ];
    if (!S.themes.some(function (t) { return t.name === selected; })) selected = S.themes.length ? S.themes[0].name : null;
    saved = snapshot();
    render();
  }

  function theme(name) {
    return S.themes.filter(function (t) { return t.name === name; })[0];
  }

  // ---- pickers: a dropdown of your saved colours or emoji and the global library
  var pickerNode = null, pickerOff = null;

  function closePicker() {
    if (pickerNode) { pickerNode.remove(); pickerNode = null; }
    if (pickerOff) { pickerOff(); pickerOff = null; }
  }

  function library(kind) {
    var lib = S && S.library ? S.library : {};
    return (kind === "emoji" ? lib.emoji : lib.colours) || [];
  }

  function openPicker(anchor, kind, onPick, clear) {
    closePicker();
    var isEmoji = kind === "emoji";
    var panel = el("div", { class: "picker", role: "dialog", "aria-label": isEmoji ? "Choose an icon" : "Choose a colour" });
    var search = el("input", { type: "search", placeholder: isEmoji ? "Search icons…" : "Search colours…", "aria-label": "Search" });
    var body = el("div", { class: "picker-body" });
    var foot = el("div", { class: "picker-foot" });
    var choose = function (value) { onPick(value); closePicker(); };

    function cell(value, title) {
      var node = el("button", { class: "pick-cell", type: "button", title: title, "aria-label": title });
      if (isEmoji) node.textContent = value; else node.style.background = value;
      node.addEventListener("click", function () { choose(value); });
      return node;
    }

    function section(title, items) {
      if (!items.length) return;
      body.appendChild(el("h4", { text: title }));
      body.appendChild(el("div", { class: "pick-grid" }, items.map(function (item) { return cell(item.value, item.title); })));
    }

    function draw() {
      var q = search.value.trim().toLowerCase();
      body.replaceChildren();
      var mine = isEmoji ? S.emoji : S.palette;
      section("Yours", Object.keys(mine).sort().filter(function (n) { return !q || n.indexOf(q) >= 0; })
        .map(function (n) { return { value: mine[n], title: n + (isEmoji ? "" : " " + mine[n]) }; }));
      var groups = {};
      library(kind).forEach(function (item) {
        if (q && (item.name + " " + item.group).toLowerCase().indexOf(q) < 0) return;
        (groups[item.group] = groups[item.group] || []).push({
          value: isEmoji ? item.glyph : item.hex,
          title: isEmoji ? item.name : item.name + " " + item.hex
        });
      });
      Object.keys(groups).forEach(function (g) { section(g, groups[g]); });
      if (!body.children.length) body.appendChild(el("p", { class: "muted", text: library(kind).length ? "Nothing matches." : "The library is not available; type your own below." }));
    }

    search.addEventListener("input", draw);
    if (isEmoji) {
      var typed = el("input", { type: "text", maxlength: "8", placeholder: "or type your own", "aria-label": "Type an emoji or symbol" });
      var use = el("button", { class: "small", type: "button", text: "Use" });
      use.addEventListener("click", function () {
        var v = typed.value.trim();
        if (!v || /^[\x00-\x7f]+$/.test(v)) { say("Enter an emoji or symbol, not plain text.", "err"); return; }
        choose(v);
      });
      foot.append(typed, use);
    } else {
      var colour = el("input", { type: "color", value: "#5b7fff", "aria-label": "Custom colour" });
      var hex = el("input", { type: "text", maxlength: "7", placeholder: "#1f6feb", "aria-label": "Custom colour hex" });
      colour.addEventListener("input", function () { hex.value = colour.value; });
      var useColour = el("button", { class: "small", type: "button", text: "Use" });
      useColour.addEventListener("click", function () {
        var v = hex.value.trim() || colour.value;
        if (!HEX.test(v)) { say("Use a colour like #1f6feb.", "err"); return; }
        choose(v);
      });
      foot.append(colour, hex, useColour);
    }
    if (clear) {
      var reset = el("button", { class: "small", type: "button", text: "Use the default" });
      reset.addEventListener("click", function () { clear(); closePicker(); });
      foot.appendChild(reset);
    }
    panel.append(search, body, foot);
    document.body.appendChild(panel);
    draw();

    var rect = anchor.getBoundingClientRect();
    var left = Math.max(8, Math.min(rect.left, window.innerWidth - panel.offsetWidth - 8));
    var below = window.innerHeight - rect.bottom, above = rect.top;
    var top = below >= Math.min(panel.offsetHeight, 360) || below >= above ? rect.bottom + 4 : rect.top - panel.offsetHeight - 4;
    panel.style.left = (left + window.scrollX) + "px";
    panel.style.top = (Math.max(8, top) + window.scrollY) + "px";
    search.focus();

    var away = function (ev) { if (!panel.contains(ev.target) && ev.target !== anchor && !anchor.contains(ev.target)) closePicker(); };
    var esc = function (ev) { if (ev.key === "Escape") { closePicker(); anchor.focus(); } };
    setTimeout(function () { document.addEventListener("mousedown", away); }, 0);
    document.addEventListener("keydown", esc);
    pickerNode = panel;
    pickerOff = function () { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }

  // An icon you choose from a dropdown. `fallback` is shown while nothing is chosen (null: always a value).
  function iconField(current, fallback, onChange, label) {
    var btn = el("button", { class: "picker-btn", type: "button", "aria-haspopup": "dialog", "aria-label": label || "Choose an icon" });
    var paint = function () {
      btn.replaceChildren(el("span", { class: current ? "" : "muted", text: current || fallback || "–" }), el("span", { class: "caret", text: "▾" }));
    };
    paint();
    btn.addEventListener("click", function () {
      openPicker(btn, "emoji", function (value) { current = value; paint(); onChange(value); },
        fallback === null ? null : function () { current = ""; paint(); onChange(""); });
    });
    return btn;
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
    var icon = iconField(t.icon, null, function (value) { t.icon = value; render(); markDirty(); }, "Icon for " + t.label);
    var stripe = textInput(t, "stripe", 200);
    stripe.placeholder = "linear-gradient(90deg, #f00 0 50%, #00f 50%) or empty";
    box.appendChild(el("div", { class: "fields" }, [
      field("Name", textInput(t, "label", 32)),
      field("Description", textInput(t, "description", 120)),
      field("Scheme", scheme),
      field("Icon", icon),
      field("Stripe under the top bar", stripe)
    ]));

    box.appendChild(preview(t, true));

    var listed = {};
    var groups = TOKEN_GROUPS.map(function (g) {
      g.tokens.forEach(function (t) { listed[t[0]] = true; });
      return g;
    });
    var others = S.tokens.filter(function (k) { return !listed[k]; });  // a token this page does not know yet
    if (others.length) groups = groups.concat([{ title: "Other", tokens: others.map(function (k) { return [k, k, ""]; }) }]);
    groups.forEach(function (group) {
      var present = group.tokens.filter(function (info) { return S.tokens.indexOf(info[0]) >= 0; });
      if (!present.length) return;
      box.appendChild(el("h3", { class: "token-group", text: group.title }));
      var grid = el("div", { class: "tokens" });
      present.forEach(function (info) {
        var key = info[0];
        var color = el("input", { type: "color", "aria-label": info[1] });
        var hex = el("input", { type: "text", maxlength: "7", "aria-label": info[1] + " hex" });
        color.value = t.tokens[key]; hex.value = t.tokens[key];
        [color, hex].forEach(function (input) { input.addEventListener("focus", function () { armed = key; }); });
        color.addEventListener("input", function () { hex.value = color.value; setToken(t, key, color.value); });
        hex.addEventListener("change", function () {
          if (HEX.test(hex.value)) { color.value = hex.value; setToken(t, key, hex.value); }
          else { hex.value = t.tokens[key]; say("Use a colour like #1f6feb.", "err"); }
        });
        var dropdown = el("button", { class: "small", type: "button", title: "Pick from your colours and the library", "aria-label": "Pick a colour for " + info[1], text: "▾" });
        dropdown.addEventListener("click", function () {
          armed = key;
          openPicker(dropdown, "colour", function (value) { setToken(t, key, value); renderEditor(); });
        });
        var name = el("span", { class: "token-name" }, [el("span", { text: info[1] }), el("small", { class: "muted", text: info[2] })]);
        grid.appendChild(el("div", { class: "token", title: key }, [name, color, hex, dropdown]));
      });
      box.appendChild(grid);
    });

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
      [["green", "Success"], ["red", "Error"], ["yellow", "Warning"], ["purple", "Highlight"]].forEach(function (pair) {
        var k = pair[0];
        var chip = el("span", { class: "st", text: pair[1] });
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

  function browse(kind) {
    var isEmoji = kind === "emoji";
    var host = $(isEmoji ? "emoji" : "palette"), id = (isEmoji ? "emoji" : "palette") + "-library";
    var node = $(id);
    var open = node ? node.open : false;
    if (!node) { node = el("details", { id: id, class: "library-browse" }); host.parentNode.insertBefore(node, host.nextSibling); }
    node.replaceChildren();
    var items = library(kind);
    node.appendChild(el("summary", { text: "Browse the library (" + items.length + ")" }));
    node.open = open;
    if (!items.length) { node.appendChild(el("p", { class: "muted", text: "The library is not available." })); return; }
    var groups = {};
    items.forEach(function (item) { (groups[item.group] = groups[item.group] || []).push(item); });
    node.appendChild(el("p", { class: "muted", text: "Click one to add it to your own list." }));
    Object.keys(groups).forEach(function (g) {
      node.appendChild(el("h4", { text: g }));
      node.appendChild(el("div", { class: "pick-grid" }, groups[g].map(function (item) {
        var value = isEmoji ? item.glyph : item.hex, map = isEmoji ? S.emoji : S.palette;
        var cell = el("button", { class: "pick-cell", type: "button", title: item.name + (isEmoji ? "" : " " + item.hex), "aria-label": "Add " + item.name });
        if (isEmoji) cell.textContent = value; else cell.style.background = value;
        cell.addEventListener("click", function () {
          if (map[item.name]) { say(item.name + " is already in your list.", ""); return; }
          map[item.name] = value; render(); say("Added " + item.name + " to your " + (isEmoji ? "emoji" : "colours") + ".", "ok");
        });
        return cell;
      })));
    });
  }

  function renderLibraries() {
    chips("palette", S.palette, function (name) { var d = el("span", { class: "dot" }); d.style.background = S.palette[name]; return d; }, function () {});
    chips("emoji", S.emoji, function (name) { return document.createTextNode(S.emoji[name]); }, function () {});
    browse("colour"); browse("emoji");
  }

  function fillSelect(node, options, value, apply) {
    node.replaceChildren();
    options.forEach(function (opt) { node.appendChild(el("option", { value: opt.value, text: opt.label })); });
    node.value = value;
    if (!node.pfBound) {
      node.pfBound = true;
      node.addEventListener("change", function () { apply(node.value); markDirty(); });
    }
  }

  function renderType() {
    var font = $("type-font"), size = $("type-size");
    if (!font || !size || !S.type) return;
    fillSelect(font, S.fonts.map(function (face) {
      return { value: face.name, label: face.label };
    }), S.type.font, function (value) { S.type.font = value; });
    fillSelect(size, S.sizes.map(function (item) {
      return item && typeof item === "object"
        ? { value: item.name, label: item.label }
        : { value: String(item), label: String(item) };
    }), S.type.size, function (value) { S.type.size = value; });
  }

  // ---- names and icons for cards, menu entries and top-bar items
  function labelValue(item, key) {
    var mine = S.labels[item.id] || {};
    return key in mine ? mine[key] : (key === "name" ? item.name : item.icon);
  }

  function setLabel(item, key, value) {
    var entry = Object.assign({}, S.labels[item.id] || {});
    var fallback = key === "name" ? item.name : item.icon;
    if (value === "" || value === fallback) delete entry[key]; else entry[key] = value;
    if (Object.keys(entry).length) S.labels[item.id] = entry; else delete S.labels[item.id];
    markDirty();
  }

  function renderLabels() {
    var box = $("labels"), filter = ($("labels-filter").value || "").toLowerCase();
    var openGroups = {};
    box.querySelectorAll("details").forEach(function (d) { openGroups[d.dataset.group] = d.open; });
    box.replaceChildren();
    var groups = {};
    CATALOG.forEach(function (item) {
      if (filter && (item.name + " " + item.group + " " + labelValue(item, "name")).toLowerCase().indexOf(filter) < 0) return;
      (groups[item.group] = groups[item.group] || []).push(item);
    });
    Object.keys(groups).forEach(function (group) {
      var details = el("details", { "data-group": group }, [el("summary", { text: group + " (" + groups[group].length + ")" })]);
      details.open = filter ? true : !!openGroups[group];
      groups[group].forEach(function (item) {
        var row = el("div", { class: "label-row" });
        var sync = function () { row.classList.toggle("changed", !!S.labels[item.id]); };
        var icon = iconField((S.labels[item.id] || {}).icon || "", item.icon || "", function (value) { setLabel(item, "icon", value); sync(); }, "Icon for " + item.name);
        var name = el("input", { type: "text", maxlength: "32", "aria-label": "Name for " + item.name, placeholder: item.name });
        name.value = (S.labels[item.id] || {}).name || "";
        name.addEventListener("input", function () { setLabel(item, "name", name.value.trim()); sync(); });
        var reset = el("button", { class: "small", type: "button", text: "Default" });
        reset.addEventListener("click", function () { delete S.labels[item.id]; name.value = ""; markDirty(); renderLabels(); });
        sync();
        var note = (item.icon || "") + " " + item.name + (item.placeholder ? " · planned" : "");
        row.append(icon, name, el("span", { class: "muted", text: note }), reset);
        details.appendChild(row);
      });
      box.appendChild(details);
    });
    if (!Object.keys(groups).length) box.appendChild(el("p", { class: "muted", text: "Nothing matches." }));
  }

  function render() {
    renderList(); renderEditor(); renderLibraries(); renderLabels(); renderType(); markDirty();
  }

  // ---- actions
  function payload() {
    return {
      themes: S.themes.map(function (t) {
        return { name: t.name, label: t.label, description: t.description, scheme: t.scheme, icon: t.icon, stripe: t.stripe, tokens: t.tokens, based_on: t.based_on || null };
      }),
      active: S.themes.filter(function (t) { return t.active; }).map(function (t) { return t.name; }),
      palette: S.palette,
      emoji: S.emoji,
      labels: S.labels,
      type: { font: S.type.font, size: S.type.size }
    };
  }

  function save() {
    say("Saving…", "");
    api("/api/themes/apply", payload()).then(function (res) {
      if (res.data.ok) {
        say("Saved. Reloading to use it…", "ok");
        setTimeout(function () { location.reload(); }, 600);
      } else {
        say("Could not save: " + (res.data.error || "unknown error"), "err");
      }
    }).catch(function () { say("Could not reach Clockwork.", "err"); });
  }

  $("labels-filter").addEventListener("input", renderLabels);
  $("save").addEventListener("click", save);
  $("revert").addEventListener("click", function () { init(); say("", ""); });
  $("reset-all").addEventListener("click", function () {
    if (!confirm("Drop all of your themes, colours, emoji, names and icons, and the shared font and page scale, and go back to the tracked themes?")) return;
    api("/api/themes/reset", {}).then(function (res) {
      if (res.data.ok) { say("Reset. Reloading…", "ok"); setTimeout(function () { location.reload(); }, 600); }
      else say("Could not reset: " + (res.data.error || "unknown error"), "err");
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
  // A theme's internal name: lowercase letters, digits and -, starting with a letter, at most 24 characters,
  // made from the title you type so you never have to think about it.
  function slugify(title) {
    var slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    if (!slug) slug = "theme";
    if (!/^[a-z]/.test(slug)) slug = "t-" + slug;
    return slug.slice(0, 24).replace(/-+$/, "");
  }

  function uniqueName(title) {
    var base = slugify(title), name = base, n = 2;
    while (theme(name)) {
      var suffix = "-" + n++;
      name = base.slice(0, 24 - suffix.length).replace(/-+$/, "") + suffix;
    }
    return name;
  }

  // Mirrors the server's rule for names shown in menus: no markup, quotes, backslashes or comment markers.
  function titleProblem(title) {
    if (!title) return "Give the theme a name.";
    if (title.length > 32) return "Use at most 32 characters for the name.";
    if (/[\x00-\x1f\x7f<>&"'`\\]|\/\*|\*\//.test(title)) return "The name cannot contain < > & quotes, backslashes or comment markers.";
    return "";
  }

  function newThemeForm() {
    var host = $("new-theme-form");
    if (!host.hidden) { host.hidden = true; return; }
    host.hidden = false;
    host.replaceChildren();
    var title = el("input", { type: "text", maxlength: "32", placeholder: "e.g. Sunset", "aria-label": "Name of the new theme" });
    var from = el("select", { "aria-label": "Start from" });
    S.themes.forEach(function (t) {
      var o = el("option", { value: t.name, text: (t.icon || "") + " " + t.label });
      o.selected = t.name === selected;
      from.appendChild(o);
    });
    var create = el("button", { class: "primary small", type: "button", text: "Create" });
    var cancel = el("button", { class: "small", type: "button", text: "Cancel" });
    var hint = el("small", { class: "muted" });
    title.addEventListener("input", function () { hint.textContent = title.value.trim() ? "Internal name: " + uniqueName(title.value.trim()) : ""; });
    var make = function () {
      var label = title.value.trim(), problem = titleProblem(label);
      if (problem) { say(problem, "err"); title.focus(); return; }
      var source = theme(from.value) || S.themes[0];
      var copy = JSON.parse(JSON.stringify(source));
      var builtin = source.origin === "builtin" ? source
        : (theme(source.based_on) || S.themes.filter(function (x) { return x.origin === "builtin"; })[0]);
      copy.name = uniqueName(label); copy.label = label; copy.origin = "custom"; copy.modified = false;
      copy.active = true; copy.based_on = builtin.name; copy.tracked = null;
      S.themes.push(copy); selected = copy.name; host.hidden = true;
      render(); markDirty();
      say("Created " + label + " from " + source.label + ". Press Save to keep it.", "ok");
    };
    create.addEventListener("click", make);
    cancel.addEventListener("click", function () { host.hidden = true; });
    title.addEventListener("keydown", function (ev) { if (ev.key === "Enter") make(); });
    host.append(el("label", {}, [document.createTextNode("Name"), title]), hint,
      el("label", {}, [document.createTextNode("Start from"), from]), el("div", { class: "add-row" }, [create, cancel]));
    title.focus();
  }

  $("new-theme").addEventListener("click", newThemeForm);

  function init() {
    api("/api/themes").then(function (res) {
      if (res.data.csrf_token) CSRF = res.data.csrf_token;
      if (res.data.ok) { load(res.data.state, res.data.catalog); say("", ""); }
      else say(res.data.error || "Could not load the themes.", "err");
    }).catch(function () { say("Could not reach Clockwork.", "err"); });
  }

  window.addEventListener("beforeunload", function (ev) {
    if (S && snapshot() !== saved) { ev.preventDefault(); ev.returnValue = ""; }
  });
  var PLACEHOLDERS = [];

  function fillPlaceholderChoices(data) {
    var groups = $("ph-groups");
    if (groups) {
      groups.replaceChildren();
      (data.groups || []).forEach(function (name) {
        groups.appendChild(el("option", { value: name }));
      });
    }
    var repo = $("ph-repo-group");
    if (!repo) return;
    var current = repo.value || "util";
    repo.replaceChildren();
    (data.repo_groups || ["util"]).forEach(function (name) {
      var option = el("option", { value: name, text: name });
      if (name === current) option.selected = true;
      repo.appendChild(option);
    });
    if (!repo.value && repo.options.length) repo.value = repo.options[0].value;
  }

  function renderPlaceholders() {
    var box = $("placeholders");
    if (!box) return;
    box.replaceChildren();
    if (!PLACEHOLDERS.length) {
      box.appendChild(el("p", { class: "muted", text: "No placeholder cards yet." }));
      return;
    }
    PLACEHOLDERS.forEach(function (card) {
      var remove = el("button", { class: "small", type: "button", text: "Remove", "aria-label": "Remove " + card.name });
      remove.addEventListener("click", function () { removePlaceholder(card); });
      box.appendChild(el("div", { class: "label-row" }, [
        el("span", { text: card.icon || "" }),
        el("span", { text: card.name }),
        el("span", { class: "muted", text: card.group + " · " + card.id + " · " + card.repo_group + "/" + card.repo }),
        remove
      ]));
    });
  }

  function placeholderBody() {
    return {
      name: $("ph-name").value.trim(),
      icon: $("ph-icon").value.trim(),
      group: $("ph-group").value.trim(),
      path: $("ph-path").value.trim(),
      repo_group: $("ph-repo-group").value,
      repo: $("ph-repo").value.trim()
    };
  }

  function unsavedThemes() {
    return S && snapshot() !== saved;
  }

  function registerPlaceholder() {
    var body = placeholderBody();
    if (!body.name) { say("Give the placeholder a name.", "err"); return; }
    if (!body.repo) { say("Name the repository the To Code item belongs to.", "err"); return; }
    if (unsavedThemes() && !confirm("You have unsaved theme edits. Registering reloads this page and drops them.")) return;
    say("Registering…", "");
    api("/api/placeholders", body).then(function (res) {
      if (!res.data.ok) { say(res.data.error || "Could not register the placeholder.", "err"); return; }
      var where = res.data.card.repo_group + "/" + res.data.card.repo;
      var extra = res.data.code === "already listed"
        ? " That To Code item was already listed."
        : " Added a To Code item in " + where + ".";
      say("Registered " + res.data.card.name + "." + extra + " Reloading…", "ok");
      setTimeout(function () { location.reload(); }, 700);
    }).catch(function () { say("Could not reach Clockwork.", "err"); });
  }

  function removePlaceholder(card) {
    if (!confirm("Remove the " + card.name + " card? The To Code item stays.")) return;
    if (unsavedThemes() && !confirm("You have unsaved theme edits. Removing reloads this page and drops them.")) return;
    api("/api/placeholders/remove", { id: card.id }).then(function (res) {
      if (!res.data.ok) { say(res.data.error || "Could not remove the placeholder.", "err"); return; }
      say("Removed " + card.name + ". Reloading…", "ok");
      setTimeout(function () { location.reload(); }, 500);
    }).catch(function () { say("Could not reach Clockwork.", "err"); });
  }

  function initPlaceholders() {
    var form = $("placeholder-form");
    if (!form) return;
    form.addEventListener("submit", function (ev) { ev.preventDefault(); registerPlaceholder(); });
    api("/api/placeholders").then(function (res) {
      if (res.data.csrf_token) CSRF = res.data.csrf_token;
      if (!res.data.ok) { say(res.data.error || "Could not load placeholder cards.", "err"); return; }
      PLACEHOLDERS = res.data.cards || [];
      fillPlaceholderChoices(res.data);
      renderPlaceholders();
    }).catch(function () { say("Could not reach Clockwork.", "err"); });
  }

  initPlaceholders();
  if (main.dataset.tool === "yes") init();
})();
