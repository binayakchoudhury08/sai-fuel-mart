/* Shared live-camera engine (proofs + attendance). No gallery, no file picking. */
(function () {
  "use strict";
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function ist(d) {
    try {
      return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true }).format(d).replace(/\bam\b/, "AM").replace(/\bpm\b/, "PM");
    } catch (e) {
      var t = new Date(d.getTime() + (330 + d.getTimezoneOffset()) * 60000), h = t.getHours(), ap = h >= 12 ? "PM" : "AM"; h = h % 12 || 12;
      return pad(t.getDate()) + "-" + pad(t.getMonth() + 1) + "-" + t.getFullYear() + ", " + pad(h) + ":" + pad(t.getMinutes()) + ":" + pad(t.getSeconds()) + " " + ap;
    }
  }
  var LC = { stream: null, loc: null, ist: ist };
  LC.stop = function () { if (LC.stream) { LC.stream.getTracks().forEach(function (t) { t.stop(); }); LC.stream = null; } };
  LC.start = function (video, facing) {
    LC.stop();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return Promise.reject(new Error("nocam"));
    return navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing || "environment" }, width: { ideal: 1920 }, height: { ideal: 1440 } }, audio: false })
      .then(function (s) { LC.stream = s; video.srcObject = s; return video.play(); });
  };
  LC.watchGps = function (onUpdate) {
    if (!navigator.geolocation) { onUpdate(null); return; }
    navigator.geolocation.watchPosition(function (p) {
      LC.loc = { lat: p.coords.latitude.toFixed(6), lng: p.coords.longitude.toFixed(6), acc: Math.round(p.coords.accuracy) }; onUpdate(LC.loc);
    }, function () { onUpdate(null); }, { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 });
  };
  /* draws the frame + a stamp band; resolves to a JPEG Blob */
  LC.snap = function (source, w0, h0, lines, mirror) {
    var max = 1600, k = Math.min(1, max / Math.max(w0, h0)), w = Math.round(w0 * k), h = Math.round(h0 * k);
    var c = document.createElement("canvas"); c.width = w; c.height = h; var g = c.getContext("2d");
    if (mirror) { g.translate(w, 0); g.scale(-1, 1); }
    g.drawImage(source, 0, 0, w, h); g.setTransform(1, 0, 0, 1, 0, 0);
    var fs = Math.max(14, Math.round(w / 38)), p = Math.round(fs * 0.6), bh = lines.length * fs * 1.3 + p * 2;
    g.fillStyle = "rgba(10,14,30,0.66)"; g.fillRect(0, h - bh, w, bh); g.textBaseline = "top";
    g.font = "600 " + fs + "px Barlow, Arial, sans-serif";
    lines.forEach(function (t, i) { g.fillStyle = i === 0 ? "#FFC857" : "#FFFFFF"; g.fillText(t, p, h - bh + p + i * fs * 1.3); });
    return new Promise(function (res) { c.toBlob(res, "image/jpeg", 0.85); });
  };
  /* 1) ask the site for one-time upload links  2) send files straight to storage  3) record it on the site */
  LC.upload = function (o) {
    function post(url, fields) {
      var fd = new FormData(); Object.keys(fields).forEach(function (k) { if (fields[k] != null) fd.append(k, fields[k]); });
      return fetch(url, { method: "POST", body: fd, credentials: "same-origin" }).then(function (r) {
        return r.json().catch(function () { return { ok: false, error: "Server error (" + r.status + ")" }; });
      });
    }
    function put(url, blob, type, part, of) {
      return new Promise(function (res, rej) {
        var x = new XMLHttpRequest(); x.open("PUT", url); x.setRequestHeader("Content-Type", type);
        x.upload.onprogress = function (e) { if (e.lengthComputable && o.progress) o.progress((part + e.loaded / e.total) / of); };
        x.onload = function () { x.status < 300 ? res() : rej(new Error("Upload refused (" + x.status + ")")); };
        x.onerror = function () { rej(new Error("No internet. Your photo is still here — try again.")); };
        x.send(blob);
      });
    }
    var start = Object.assign({ _csrf: o.csrf, video: o.video ? "1" : "0", video_ext: o.videoExt || "" }, o.startFields || {});
    return post(o.startUrl, start).then(function (s) {
      if (!s.ok) throw new Error(s.error || "Could not start the upload");
      var parts = o.video && s.video_url ? 2 : 1;
      return put(s.photo_url, o.photo, "image/jpeg", 0, parts)
        .then(function () { return parts === 2 ? put(s.video_url, o.video, o.video.type || "video/webm", 1, 2) : null; })
        .then(function () { return post(o.finishUrl, Object.assign({ _csrf: o.csrf, ticket: s.ticket }, o.finishFields || {})); });
    }).then(function (r) { if (!r.ok) throw new Error(r.error || "Could not save"); return r; });
  };
  window.LiveCam = LC;
})();
