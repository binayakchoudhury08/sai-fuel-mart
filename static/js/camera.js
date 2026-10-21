/* Live camera for proofs. No gallery, no file picking: the photo is taken from the live camera,
   stamped (type, date/time, GPS, user, pump) on the picture itself, and uploaded. */
(function () {
  "use strict";
  var root = document.getElementById("cam");
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var video = $("video"), preview = $("preview"), shoot = $("shoot"), retake = $("retake"), flip = $("flip"),
      rec = $("rec"), gpsEl = $("gps"), recTime = $("rectime"), msg = $("msg"), up = $("upload");
  var state = { type: ($("camtitle").textContent || "").trim(), facing: "environment", stream: null,
                photo: null, takenAt: 0, mode: "live", video: null, videoExt: "", loc: null, recorder: null };

  function say(t, bad) { msg.textContent = t || ""; msg.style.color = bad ? "var(--stop)" : ""; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function stamp(d) {
    // always Indian time, whatever time zone the phone is set to
    try {
      return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true }).format(d).replace(/\bam\b/, "AM").replace(/\bpm\b/, "PM");
    } catch (e) {
      var ist = new Date(d.getTime() + (330 + d.getTimezoneOffset()) * 60000);
      var m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][ist.getMonth()];
      var h = ist.getHours(), ap = h >= 12 ? "PM" : "AM"; h = h % 12 || 12;
      return pad(ist.getDate()) + " " + m + " " + ist.getFullYear() + ", " + pad(h) + ":" + pad(ist.getMinutes()) + ":" + pad(ist.getSeconds()) + " " + ap;
    }
  }

  // ---------- step 1: type
  document.querySelectorAll("[data-type]").forEach(function (b) {
    b.addEventListener("click", function () { state.type = b.dataset.type; $("camtitle").textContent = state.type;
      $("step-type").hidden = true; $("step-cam").hidden = false; start(); });
  });
  $("change").addEventListener("click", function () { stop(); $("step-cam").hidden = true; $("step-save").hidden = true; $("step-type").hidden = false; });

  // ---------- location (keeps improving until the photo is taken)
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(function (p) {
      state.loc = { lat: p.coords.latitude.toFixed(6), lng: p.coords.longitude.toFixed(6), acc: Math.round(p.coords.accuracy) };
      gpsEl.textContent = "GPS ±" + state.loc.acc + " m"; gpsEl.className = "badge locked";
    }, function () { gpsEl.textContent = "No location (allow location)"; gpsEl.className = "badge open"; },
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 });
  } else { gpsEl.textContent = "No location"; }

  // ---------- camera
  function stop() { if (state.stream) { state.stream.getTracks().forEach(function (t) { t.stop(); }); state.stream = null; } }
  function start() {
    preview.hidden = true; video.hidden = false; retake.hidden = true; shoot.disabled = false;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { useFallback(); return; }
    stop();
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: state.facing }, width: { ideal: 1920 }, height: { ideal: 1440 } }, audio: false })
      .then(function (s) { state.stream = s; video.srcObject = s; return video.play(); })
      .catch(function (e) {
        if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) say("Camera permission was refused. Allow the camera for this site/app and try again.", true);
        useFallback();
      });
  }
  function useFallback() { $("fallback").hidden = false; shoot.hidden = true; flip.hidden = true; rec.hidden = true; state.mode = "camera-input"; }
  flip.addEventListener("click", function () { state.facing = state.facing === "environment" ? "user" : "environment"; start(); });
  if (state.type) start();

  function draw(source, sw, sh, when) {
    var max = 1600, k = Math.min(1, max / Math.max(sw, sh));
    var w = Math.round(sw * k), h = Math.round(sh * k);
    var c = document.createElement("canvas"); c.width = w; c.height = h;
    var g = c.getContext("2d"); g.drawImage(source, 0, 0, w, h);
    var fs = Math.max(14, Math.round(w / 38)), pad = Math.round(fs * 0.6);
    var lines = [state.type + " · " + root.dataset.station,
                 stamp(when) + " IST",
                 (state.loc ? "GPS " + state.loc.lat + ", " + state.loc.lng + " ±" + state.loc.acc + " m" : "GPS not available") + " · by " + root.dataset.user];
    var bh = lines.length * (fs * 1.3) + pad * 2;
    g.fillStyle = "rgba(0,0,0,0.62)"; g.fillRect(0, h - bh, w, bh);
    g.fillStyle = "#F2B233"; g.font = "600 " + fs + "px Barlow, Arial, sans-serif"; g.textBaseline = "top";
    lines.forEach(function (t, i) { g.fillStyle = i === 0 ? "#F2B233" : "#FFFFFF"; g.fillText(t, pad, h - bh + pad + i * fs * 1.3); });
    return c;
  }
  function done(canvas) {
    canvas.toBlob(function (b) {
      state.photo = b; preview.src = URL.createObjectURL(b); preview.hidden = false; video.hidden = true;
      retake.hidden = false; $("step-save").hidden = false; say("Photo ready. Check it, add details and save.");
      $("step-save").scrollIntoView({ behavior: "smooth", block: "start" });
    }, "image/jpeg", 0.85);
  }
  shoot.addEventListener("click", function () {
    if (!video.videoWidth) { say("Camera is still starting…", true); return; }
    state.takenAt = Date.now(); state.mode = "live";
    done(draw(video, video.videoWidth, video.videoHeight, new Date(state.takenAt)));
    if (navigator.vibrate) navigator.vibrate(30);
  });
  retake.addEventListener("click", function () { state.photo = null; start(); $("step-save").hidden = true; });

  // fallback: the phone's own camera opens directly; a gallery photo is refused (it is not "just taken")
  $("camfile").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return;
    if (Date.now() - f.lastModified > 10 * 60 * 1000) { say("That picture was not taken just now. Use the camera.", true); e.target.value = ""; return; }
    var img = new Image(); img.onload = function () { state.takenAt = f.lastModified || Date.now(); state.mode = "camera-input";
      done(draw(img, img.naturalWidth, img.naturalHeight, new Date(state.takenAt))); };
    img.src = URL.createObjectURL(f);
  });

  // ---------- optional live video (max 30 s)
  if (!window.MediaRecorder) rec.hidden = true;
  rec.addEventListener("click", function () {
    if (state.recorder) { state.recorder.stop(); return; }
    var types = ["video/mp4", "video/webm;codecs=vp8,opus", "video/webm"], mime = "";
    for (var i = 0; i < types.length; i++) if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(types[i])) { mime = types[i]; break; }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: state.facing }, width: { ideal: 1280 } }, audio: true })
      .catch(function () { return navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: state.facing } } }); })
      .then(function (s) {
        stop(); state.stream = s; video.srcObject = s; video.hidden = false; preview.hidden = true; video.play();
        var chunks = [], r = new MediaRecorder(s, mime ? { mimeType: mime, videoBitsPerSecond: 1500000 } : undefined);
        state.recorder = r; var t0 = Date.now();
        r.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
        r.onstop = function () {
          clearInterval(timer); recTime.hidden = true; rec.textContent = "● Video"; state.recorder = null;
          var type = (r.mimeType || mime || "video/webm").split(";")[0];
          state.video = new Blob(chunks, { type: type }); state.videoExt = type.indexOf("mp4") >= 0 ? "mp4" : "webm";
          $("videoinfo").hidden = false; $("vlabel").textContent = "Video ready · " + Math.round((Date.now() - t0) / 1000) + " s";
          if (!state.photo) say("Video recorded. Now take the photo too (a photo is required).");
          start();
        };
        r.start(1000); rec.textContent = "■ Stop"; recTime.hidden = false;
        var timer = setInterval(function () { var s2 = Math.round((Date.now() - t0) / 1000); recTime.textContent = "REC " + s2 + " s / 30";
          if (s2 >= 30) r.stop(); }, 500);
      }).catch(function () { say("Could not start video recording on this phone.", true); });
  });
  $("vremove").addEventListener("click", function () { state.video = null; $("videoinfo").hidden = true; });

  // ---------- upload
  up.addEventListener("click", function () {
    if (!state.photo) { say("Take the photo first.", true); return; }
    var fd = new FormData();
    fd.append("_csrf", $("csrf").value); fd.append("_ft", $("ft").value);
    fd.append("category", state.type); fd.append("stock_status", $("status").value); fd.append("note", $("note").value);
    fd.append("captured_at", String(state.takenAt)); fd.append("capture_mode", state.mode);
    if (state.loc) { fd.append("lat", state.loc.lat); fd.append("lng", state.loc.lng); fd.append("acc", state.loc.acc); }
    fd.append("photo", state.photo, "proof.jpg");
    if (state.video) fd.append("video", state.video, "proof." + state.videoExt);
    var x = new XMLHttpRequest(), bar = $("upbar"); bar.hidden = false; up.disabled = true; up.textContent = "Saving…";
    x.upload.onprogress = function (e) { if (e.lengthComputable) bar.firstElementChild.style.width = Math.round(100 * e.loaded / e.total) + "%"; };
    x.onload = function () {
      var r = {}; try { r = JSON.parse(x.responseText); } catch (e) {}
      if (x.status === 200 && r.ok) { say("Saved."); stop(); location.href = r.redirect; return; }
      up.disabled = false; up.textContent = "Save proof"; say(r.error || ("Could not save (" + x.status + "). Check the internet and try again."), true);
    };
    x.onerror = function () { up.disabled = false; up.textContent = "Save proof"; say("No internet. Your photo is still here — try again.", true); };
    x.open("POST", root.dataset.post); x.send(fd);
  });
  window.addEventListener("pagehide", stop);
})();
