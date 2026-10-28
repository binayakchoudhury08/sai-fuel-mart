/* Sai Fuel Mart v2. Everything here is a convenience preview: the server
   recalculates and validates every figure before saving. */
(function(){
  "use strict";
  function num(v){ v = String(v||"").replace(/,/g,"").trim(); var n = parseFloat(v); return isFinite(n) ? n : 0; }
  function inr(n){
    var neg = n < 0; n = Math.abs(n);
    var s = n.toFixed(2), parts = s.split("."), w = parts[0];
    if (w.length > 3){ var head = w.slice(0,-3), tail = w.slice(-3);
      head = head.replace(/\B(?=(\d{2})+(?!\d))/g, ","); w = head + "," + tail; }
    return (neg ? "−" : "") + "₹" + w + "." + parts[1];
  }

  // 1. One tap = one save. Disable the button and show progress.
  document.addEventListener("submit", function(e){
    var f = e.target;
    if (f.dataset.confirm && !window.confirm(f.dataset.confirm)) { e.preventDefault(); return; }
    if (f.dataset.busy === "1") { e.preventDefault(); return; }
    f.dataset.busy = "1";
    var b = e.submitter || f.querySelector("button[type=submit],button:not([type])");
    if (b && !f.hasAttribute("data-download")) { b.dataset.label = b.textContent; b.disabled = true; b.textContent = "Saving…"; }
    if (f.hasAttribute("data-download")) setTimeout(function(){ f.dataset.busy = ""; }, 3000);
  }, true);
  window.addEventListener("pageshow", function(){
    document.querySelectorAll("form[data-busy='1']").forEach(function(f){
      f.dataset.busy = ""; f.querySelectorAll("button[disabled]").forEach(function(b){ b.disabled=false; if(b.dataset.label) b.textContent=b.dataset.label; });
    });
  });

  // 2. Scrolling over a focused number box must never change the number.
  document.addEventListener("wheel", function(e){
    if (document.activeElement && document.activeElement.type === "number" && document.activeElement === e.target) e.target.blur();
  }, {passive:true});

  // 3. Meter reading preview
  function meterRow(tr){
    var o = tr.querySelector("[data-open]"), c = tr.querySelector("[data-close]"), t = tr.querySelector("[data-test]");
    var out = tr.querySelector("[data-sale]"), amt = tr.querySelector("[data-amount]");
    if (!c || !out) return;
    var rate = num(tr.dataset.rate);
    var open = o ? num(o.value || o.dataset.value) : 0;
    if (c.value === "") { out.textContent = "–"; if (amt) amt.textContent = "–"; tr.classList.add("missing"); return; }
    tr.classList.remove("missing");
    var s = num(c.value) - open - num(t && t.value);
    out.textContent = s.toFixed(2);
    out.classList.toggle("neg", s < 0);
    if (amt) amt.textContent = s < 0 ? "Check reading" : inr(s * rate);
  }
  document.querySelectorAll("tr[data-meter]").forEach(function(tr){
    tr.addEventListener("input", function(){ meterRow(tr); }); meterRow(tr);
  });
  document.querySelectorAll("[data-override]").forEach(function(cb){
    cb.addEventListener("change", function(){
      var tr = cb.closest("tr"), box = tr.querySelector("[data-open]"), why = tr.querySelector("[data-reason]");
      box.readOnly = !cb.checked; if (why) why.hidden = !cb.checked; if (!cb.checked) box.value = box.dataset.value; meterRow(tr);
    });
  });

  // 4. Note counter
  var den = document.querySelector("[data-denoms]");
  if (den){
    var calc = function(){
      var total = 0;
      den.querySelectorAll("[data-den]").forEach(function(i){ total += num(i.value) * num(i.dataset.den); });
      var coins = den.querySelector("[data-coins]"); total += num(coins && coins.value);
      var out = den.querySelector("[data-den-total]"); if (out) out.textContent = inr(total);
    };
    den.addEventListener("input", calc); calc();
  }

  // 5. Credit entry: litres <-> amount preview
  document.querySelectorAll("[data-fuelform]").forEach(function(f){
    var sel = f.querySelector("[name=fuel_code]"), q = f.querySelector("[name=qty]"), a = f.querySelector("[name=amount]"),
        r = f.querySelector("[name=rate]"), out = f.querySelector("[data-preview]");
    function rate(){ var o = sel && sel.options[sel.selectedIndex]; return num((r && r.value) || (o && o.dataset.rate)); }
    function upd(){
      if (r && sel && !r.dataset.touched) r.placeholder = (sel.options[sel.selectedIndex].dataset.rate || "");
      var rt = rate(); if (!out) return;
      if (num(q.value) > 0) out.textContent = num(q.value).toFixed(2) + " × " + rt.toFixed(2) + " = " + inr(num(q.value)*rt);
      else if (num(a.value) > 0 && rt > 0) out.textContent = inr(num(a.value)) + " ÷ " + rt.toFixed(2) + " = " + (num(a.value)/rt).toFixed(2) + " L";
      else out.textContent = "";
    }
    if (r) r.addEventListener("input", function(){ r.dataset.touched = "1"; });
    f.addEventListener("input", upd); f.addEventListener("change", upd); upd();
  });

  // 6. Lube sale: show party chooser for credit; preview amount
  document.querySelectorAll("[data-lubeform]").forEach(function(f){
    var mode = f.querySelector("[name=pay_mode]"), party = f.querySelector("[data-party]"),
        prod = f.querySelector("[name=product_id]"), q = f.querySelector("[name=qty]"), out = f.querySelector("[data-preview]");
    function upd(){
      if (party) party.hidden = mode.value !== "Credit";
      var o = prod.options[prod.selectedIndex], rate = num(o && o.dataset.rate), stock = o && o.dataset.stock;
      if (out) out.textContent = prod.value ? (inr(num(q.value) * rate) + (stock !== undefined ? "  ·  in stock: " + stock : "")) : "";
    }
    f.addEventListener("change", upd); f.addEventListener("input", upd); upd();
  });

  // 7. Instant filter for long lists
  document.querySelectorAll("[data-filter]").forEach(function(inp){
    var target = document.querySelector(inp.dataset.filter);
    inp.addEventListener("input", function(){
      var q = inp.value.toLowerCase();
      target.querySelectorAll("tbody tr").forEach(function(tr){ tr.hidden = q && tr.textContent.toLowerCase().indexOf(q) < 0; });
    });
  });

  // 8. Date pickers that navigate
  document.querySelectorAll("[data-gotodate]").forEach(function(inp){
    inp.addEventListener("change", function(){ if (inp.value) location.href = inp.dataset.gotodate.replace("__D__", inp.value); });
  });

  // 9. Location for photo proofs
  var geo = document.querySelector("[data-geo]");
  if (geo && navigator.geolocation){
    navigator.geolocation.getCurrentPosition(function(p){
      geo.querySelector("[name=lat]").value = p.coords.latitude.toFixed(6);
      geo.querySelector("[name=lng]").value = p.coords.longitude.toFixed(6);
    }, function(){}, {timeout:8000});
  }

  // 10. Close the mobile menu after choosing
  document.querySelectorAll(".rail a").forEach(function(a){ a.addEventListener("click", function(){ var m=document.getElementById("more"); if(m) m.checked=false; }); });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(function(){});
})();

/* Transport entry: fill vehicles for the chosen transporter, preview diesel + cash = total */
(function(){
  function num(v){ v=String(v||"").replace(/,/g,""); var n=parseFloat(v); return isFinite(n)?n:0; }
  function inr(n){ var s=Math.abs(n).toFixed(2).split("."), w=s[0];
    if(w.length>3){ w=w.slice(0,-3).replace(/\B(?=(\d{2})+(?!\d))/g,",")+","+w.slice(-3); } return (n<0?"−":"")+"₹"+w+"."+s[1]; }
  document.querySelectorAll("[data-transport]").forEach(function(f){
    var party=f.querySelector("[name=party]"), veh=f.querySelector("[data-vehicle]"), fuel=f.querySelector("[name=fuel_code]"),
        q=f.querySelector("[name=qty]"), amt=f.querySelector("[name=amount]"), rate=f.querySelector("[name=rate]"),
        cash=f.querySelector("[name=cash_taken]"), out=f.querySelector("[data-tpreview]");
    var list = veh && veh.getAttribute("list") ? document.getElementById(veh.getAttribute("list")) : null;
    function fillVehicles(){
      if (!party || !party.options || party.selectedIndex < 0) return;
      var o=party.options[party.selectedIndex], vs=((o && o.dataset.vehicles)||"").split(",").map(function(x){return x.trim();}).filter(Boolean);
      if (list){ list.innerHTML = vs.map(function(v){return '<option value="'+v+'">';}).join(""); }
      if (veh && !veh.value && vs.length===1) veh.value = vs[0];
    }
    function upd(){
      var fo = fuel && fuel.options[fuel.selectedIndex], r = num(rate && rate.value) || num(fo && fo.dataset.rate);
      if (rate && !rate.value && fo) rate.placeholder = fo.dataset.rate || "";
      var qty = num(q && q.value); if (!qty && amt && num(amt.value) && r) qty = num(amt.value)/r;
      var fa = Math.round(qty*r), c = Math.round(num(cash && cash.value));   // whole rupees, like the saved slip
      var rs = function(n){ return inr(n).replace(/\.00$/, ""); };
      if (out) out.textContent = (qty||c) ? (qty ? qty.toFixed(2)+" L × "+r.toFixed(2)+" = "+rs(fa) : "") + (c ? (qty?"  + cash ":"Cash ")+rs(c) : "") + "  =  Total "+rs(fa+c) : "";
    }
    if (party && party.tagName === "SELECT") party.addEventListener("change", function(){ if(veh) veh.value=""; fillVehicles(); });
    fillVehicles(); f.addEventListener("input", upd); f.addEventListener("change", upd); upd();
  });
})();

/* Tank page: "synced X min ago" badges ticking every second + refresh from /api/tank-status every 30 s (old behaviour) */
(function(){
  var box = document.querySelector("[data-tanklive]");
  if (!box) return;
  function ago(iso){
    if (!iso) return null;
    var mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return {t:"synced just now", cls:"locked"};
    if (mins < 60) return {t:"synced " + mins + " min ago", cls: mins < 30 ? "locked" : "open"};
    var h = Math.floor(mins/60);
    if (h < 24) return {t:"synced " + h + " h ago", cls: h < 2 ? "open" : "stop"};
    return {t:"synced " + Math.floor(h/24) + " d ago", cls:"stop"};
  }
  function badges(){
    box.querySelectorAll("[data-sync]").forEach(function(b){
      if (b.dataset.src !== "atg") return;
      var a = ago(b.dataset.at); if (!a) return;
      b.textContent = "ATG · " + a.t; b.className = "badge " + a.cls;
    });
  }
  function fmt(n, d){ return n == null ? "—" : Number(n).toLocaleString("en-IN", {minimumFractionDigits:d, maximumFractionDigits:d}); }
  function poll(){
    if (document.hidden) return;
    fetch("/api/tank-status", {credentials:"same-origin"}).then(function(r){ return r.ok ? r.json() : null; }).then(function(data){
      if (!data || data.status !== "success") return;
      data.tanks.forEach(function(t){
        var card = box.querySelector('[data-tank="' + t.tank_id + '"]'); if (!card) return;
        var set = function(k, v){ var el = card.querySelector('[data-v="' + k + '"]'); if (el) el.textContent = v; };
        if (t.reading_date === new Date(Date.now() + 19800000).toISOString().slice(0,10)) {
          set("stock", fmt(t.stock, 0)); set("variance", t.variance == null ? "—" : fmt(t.variance, 2));
          set("fill", t.fill_pct == null ? "—" : Math.round(t.fill_pct) + "% of " + fmt(t.capacity, 0) + " L");
          var bar = card.querySelector('[data-v="fillbar"]'); if (bar && t.fill_pct != null) bar.style.width = Math.min(t.fill_pct, 100) + "%";
          var b = card.querySelector("[data-sync]"); if (b){ b.dataset.at = t.updated_at; b.dataset.src = t.source; }
        }
        if (t.days_left != null) set("days", t.days_left);
      });
      badges();
    }).catch(function(){});
  }
  badges(); setInterval(badges, 1000); setInterval(poll, 30000);
})();

/* Phone "More" sheet: close after choosing, on Escape / Android back, and open with Enter/Space */
(function(){
  var more = document.getElementById("more"); if (!more) return;
  document.querySelectorAll(".sheet a").forEach(function(a){ a.addEventListener("click", function(){ more.checked = false; }); });
  document.addEventListener("keydown", function(e){ if (e.key === "Escape") more.checked = false; });
  document.querySelectorAll('label[for="more"][role="button"]').forEach(function(l){
    l.addEventListener("keydown", function(e){ if (e.key === "Enter" || e.key === " ") { e.preventDefault(); more.checked = !more.checked; } });
  });
  more.addEventListener("change", function(){ if (more.checked) history.pushState({sheet:1}, ""); });
  window.addEventListener("popstate", function(){ if (more.checked) more.checked = false; });
})();


/* ☰ : collapse the sidebar on computers, open the menu sheet on phones; remembered on this device */
(function(){
  var b = document.querySelector("[data-hamb]"); if (!b) return;
  try { if (localStorage.getItem("rail-min") === "1") document.body.classList.add("rail-min"); } catch (e) {}
  b.addEventListener("click", function(){
    if (window.matchMedia("(max-width:900px)").matches) { var m = document.getElementById("more"); if (m) m.checked = !m.checked; return; }
    document.body.classList.toggle("rail-min");
    try { localStorage.setItem("rail-min", document.body.classList.contains("rail-min") ? "1" : "0"); } catch (e) {}
  });
  document.addEventListener("click", function(e){
    var d = document.querySelector(".usermenu[open]"); if (d && !d.contains(e.target)) d.removeAttribute("open");
  });
})();


/* Tabs: <nav data-tabs><a data-show="group" href="#id">…</a></nav> and elements with data-tab="group".
   The address #id opens the tab that holds that element. */
(function(){
  var nav = document.querySelector("[data-tabs]"); if (!nav) return;
  var links = nav.querySelectorAll("a[data-show]"); if (!links.length) return;
  function show(group){
    document.querySelectorAll("[data-tab]").forEach(function(el){ el.hidden = el.dataset.tab !== group; });
    links.forEach(function(a){ a.classList.toggle("on", a.dataset.show === group); });
  }
  nav.addEventListener("click", function(e){ var a = e.target.closest("a[data-show]"); if (!a) return; e.preventDefault();
    show(a.dataset.show); history.replaceState(null, "", a.getAttribute("href")); });
  var h = (location.hash || "").slice(1), el = h && document.getElementById(h), holder = el && (el.closest("[data-tab]") || el);
  show(holder && holder.dataset.tab ? holder.dataset.tab : links[0].dataset.show);
})();

/* links to a closed <details id=…> open it */
(function(){ function open(){ var el = location.hash && document.getElementById(location.hash.slice(1)); if (el && el.tagName === "DETAILS") { el.open = true; el.scrollIntoView({block:"start"}); } }
  window.addEventListener("hashchange", open); open(); })();

/* ☀/☾ light–dark, remembered on this device */
(function(){
  try { if (localStorage.getItem("theme") === "dark") document.body.classList.add("dark"); } catch (e) {}
  var b = document.querySelector("[data-theme-toggle]"); if (!b) return;
  b.addEventListener("click", function(){ document.body.classList.toggle("dark");
    try { localStorage.setItem("theme", document.body.classList.contains("dark") ? "dark" : "light"); } catch (e) {} });
})();
