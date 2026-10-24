/* Live-photo attendance: tap a staff member → live camera → photo (stamped) → marked present. */
(function () {
  "use strict";
  var root = document.getElementById("att"), sheet = document.getElementById("camsheet");
  if (!root || !sheet || !window.LiveCam) return;
  var LC = window.LiveCam, $ = function (id) { return document.getElementById(id); };
  var video = $("camvideo"), prev = $("camprev"), shoot = $("camshoot"), save = $("camsave"), msg = $("cammsg");
  var cur = { id: null, name: "", kind: "in", blob: null, at: 0, facing: "environment" };
  LC.watchGps(function (l) { $("camgps").textContent = l ? "GPS ±" + l.acc + " m" : "No GPS"; $("camgps").className = "badge " + (l ? "locked" : "open"); });
  function say(t, bad) { msg.textContent = t || ""; msg.style.color = bad ? "#FF8A9B" : "#fff"; }
  function open(id, name, kind) {
    cur = { id: id, name: name, kind: kind || "in", blob: null, at: 0, facing: cur.facing };
    $("camname").textContent = name + (cur.kind === "out" ? " — check out" : " — check in");
    save.textContent = cur.kind === "out" ? "Check out" : "Mark present"; sheet.hidden = false; prev.hidden = true; video.hidden = false; save.disabled = true; say("Point the camera at " + name + " and take the photo.");
    LC.start(video, cur.facing).catch(function () { say("Camera not available. Allow the camera for this app/site.", true); });
  }
  function close() { LC.stop(); sheet.hidden = true; }
  document.querySelectorAll("[data-snap]").forEach(function (b) { b.addEventListener("click", function () { open(b.dataset.snap, b.dataset.name, b.dataset.kind); }); });
  $("camclose").addEventListener("click", close);
  $("camflip").addEventListener("click", function () { cur.facing = cur.facing === "environment" ? "user" : "environment"; video.hidden = false; prev.hidden = true; LC.start(video, cur.facing); });
  shoot.addEventListener("click", function () {
    if (!video.videoWidth) { say("Camera is still starting…", true); return; }
    cur.at = Date.now();
    var l = LC.loc;
    LC.snap(video, video.videoWidth, video.videoHeight, [
      (cur.kind === "out" ? "Check out · " : "Attendance · ") + cur.name, LC.ist(new Date(cur.at)) + " IST",
      (l ? "GPS " + l.lat + ", " + l.lng + " ±" + l.acc + " m" : "GPS not available") + " · by " + root.dataset.user + " · " + root.dataset.station
    ], cur.facing === "user").then(function (b) {
      cur.blob = b; prev.src = URL.createObjectURL(b); prev.hidden = false; video.hidden = true; save.disabled = false;
      say("Looks right? Press “Mark present”. Or press the round button again to retake.");
    });
  });
  shoot.addEventListener("dblclick", function (e) { e.preventDefault(); });
  prev.addEventListener("click", function () { prev.hidden = true; video.hidden = false; save.disabled = true; });
  save.addEventListener("click", function () {
    if (!cur.blob) return;
    save.disabled = true; save.textContent = "Saving…";
    var l = LC.loc || {};
    LC.upload({ csrf: $("csrf").value, startUrl: root.dataset.start, finishUrl: root.dataset.post,
      startFields: { staff_id: cur.id, work_date: root.dataset.date, kind: cur.kind }, photo: cur.blob,
      finishFields: { staff_id: cur.id, work_date: root.dataset.date, kind: cur.kind, captured_at: String(cur.at), lat: l.lat, lng: l.lng },
      progress: function (f) { say("Uploading " + Math.round(f * 100) + "%"); }
    }).then(function () { say(cur.name + (cur.kind === "out" ? " checked out ✓" : " marked present ✓")); setTimeout(function () { close(); location.reload(); }, 600); })
      .catch(function (e) { save.disabled = false; save.textContent = cur.kind === "out" ? "Check out" : "Mark present"; say(e.message, true); });
  });
})();
