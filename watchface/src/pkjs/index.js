/**
 * GUAI watchface PebbleKit JS.
 *
 * Receives health snapshots from C over AppMessage, buffers them in
 * localStorage while offline, and uploads them to the GUAI server with the
 * configured bearer token. HTTP runs from the phone, never from the watch.
 *
 * Configuration (server URL + token) is set via the app's config page and
 * stored in localStorage.
 */

var SERVER_KEY = "GUAI_SERVER";
var TOKEN_KEY = "GUAI_TOKEN";
var QUEUE_KEY = "GUAI_QUEUE";
var NEXT_POLL_KEY = "GUAI_NEXT_POLL";

var DEVICE_NAME = "pebble";

function settingsReady() {
  return !!localStorage.getItem(SERVER_KEY) && !!localStorage.getItem(TOKEN_KEY);
}

function utcIso(epochSeconds) {
  return new Date(epochSeconds * 1000).toISOString(); // ends with Z
}

function localDateIso(epochSeconds) {
  var d = new Date(epochSeconds * 1000);
  var mm = d.getMonth() + 1;
  var dd = d.getDate();
  return d.getFullYear() + "-" + (mm < 10 ? "0" : "") + mm + "-" + (dd < 10 ? "0" : "") + dd;
}

function readQueue() {
  // The queue blob may be corrupt (e.g. truncated by an interrupted write); never let
  // that break the uploader — a parse failure means "empty queue".
  try {
    return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function enqueue(entry) {
  var q = readQueue();
  var key = entry.type + "|" + entry.dedupeKey;
  q = q.filter(function (e) { return e.type + "|" + e.dedupeKey !== key; });
  q.push(entry);
  if (q.length > 500) q = q.slice(q.length - 500);
  localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
}

function nextEntry() {
  return readQueue()[0];
}

function dropHead() {
  var q = readQueue();
  q.shift();
  localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
}

// pypkjs provides XMLHttpRequest, not fetch — keep HTTP here so it also runs on real devices.
function httpPost(url, token, body, done) {
  var xhr = new XMLHttpRequest();
  xhr.open("POST", url, true);
  xhr.setRequestHeader("Content-Type", "application/json");
  xhr.setRequestHeader("Authorization", "Bearer " + token);
  xhr.timeout = 15000;
  xhr.onload = function () { done(xhr.status); };
  xhr.onerror = function () { done(0); };
  xhr.ontimeout = function () { done(0); };
  xhr.send(JSON.stringify(body));
}

function upload() {
  var entry = nextEntry();
  if (!entry) return;
  if (!settingsReady()) { console.log("pkjs: queue has " + entry.type + " but not configured yet"); return; }

  var server = localStorage.getItem(SERVER_KEY).replace(/\/$/, "");
  var token = localStorage.getItem(TOKEN_KEY);

  console.log("pkjs: POST " + server + "/api/sync type=" + entry.type);
  httpPost(server + "/api/sync", token, entry.body, function (status) {
    console.log("pkjs: upload type=" + entry.type + " status=" + status);
    if (status === 200) {
      dropHead();
      setTimeout(upload, 500); // continue the queue
    } else {
      setTimeout(upload, 15000); // rejected/offline; keep buffered
    }
  });
}

// --- C -> JS message handling -------------------------------------------

Pebble.addEventListener("appmessage", function (e) {
  var p = e.payload;
  var ts = p.TIMESTAMP;
  var bpm = p.HR_BPM;

  console.log("pkjs: appmessage type=" + p.TYPE);
  if (p.TYPE === "activity") {
    enqueue({
      type: "activity",
      dedupeKey: ts,
      body: {
        collected_at: utcIso(ts),
        device: DEVICE_NAME,
        activity: {
          steps: p.STEPS,
          distance_meters: p.DISTANCE_METERS / 10,
          active_seconds: p.ACTIVE_SECONDS,
          resting_kcal: p.RESTING_KCAL / 10,
          active_kcal: p.ACTIVE_KCAL / 10
        }
      }
    });
  } else if (p.TYPE === "heart_rate" && bpm) {
    enqueue({
      type: "heart_rate",
      dedupeKey: ts,
      body: {
        heart_rates: [{ at: utcIso(ts), bpm: bpm }]
      }
    });
  } else if (p.TYPE === "sleep") {
    enqueue({
      type: "sleep",
      dedupeKey: ts,
      body: {
        sleep: [{
          night_date: localDateIso(ts),
          sleep_seconds: p.SLEEP_SECONDS,
          restful_seconds: p.SLEEP_RESTFUL_SECONDS
        }]
      }
    });
  }

  upload();
});

var sleepRequested = false;
function requestSleepOnce() {
  // Ask C for the previous night's sleep exactly once per app session.
  if (sleepRequested) return;
  sleepRequested = true;
  Pebble.sendAppMessage({ REQUEST: "sleep_request" }, function () {}, function () {});
}

Pebble.addEventListener("ready", function () {
  console.log("pkjs: ready, server=" + localStorage.getItem(SERVER_KEY) + " tokenSet=" + !!localStorage.getItem(TOKEN_KEY));
  upload();
  requestSleepOnce();
  // Periodic flush while the watchface is open.
  setInterval(upload, 60000);
});

// --- Configuration page -------------------------------------------------

function htmlEscape(s) {
  // Attribute values must be HTML-escaped, not URI-encoded, so values like
  // "http://host:8080?x=1&y=2" display and re-save unmodified.
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function configHtml(name) {
  var srv = htmlEscape(localStorage.getItem(SERVER_KEY) || "");
  var tok = htmlEscape(localStorage.getItem(TOKEN_KEY) || "");
  return "data:text/html;charset=utf-8," +
    encodeURIComponent(
      '<!DOCTYPE html><html><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width, initial-scale=1">' +
      // Explicit light scheme: the webview may follow the OS dark mode, which
      // renders a dark background while text/inputs default to black.
      '<meta name="color-scheme" content="light">' +
      '<style>' +
      'html{color-scheme:light}' +
      'body{background:#ffffff;color:#111111;font-family:sans-serif;padding:16px;max-width:480px;margin:0 auto}' +
      'input{width:100%;box-sizing:border-box;padding:8px;font-size:16px;background:#ffffff;color:#111111;border:1px solid #999;border-radius:6px}' +
      'button{margin-top:12px;padding:8px 16px;font-size:16px;background:#111111;color:#ffffff;border:0;border-radius:6px}' +
      '</style></head>' +
      '<body>' +
      '<h2>GUAI</h2>' +
      '<form id="f">' +
      '<label>Server URL<br><input id="srv" value="' + srv + '"></label><br><br>' +
      '<label>Token<br><input id="tok" value="' + tok + '"></label><br><br>' +
      '<button type="submit">Save</button></form>' +
      '<script>document.getElementById("f").onsubmit=function(){' +
      'var send=function(){var opts={srv:document.getElementById("srv").value,' +
      'tok:document.getElementById("tok").value};location.href=' +
      '"pebblejs://close#"+encodeURIComponent(JSON.stringify(opts))};' +
      'setTimeout(send,100);return false;};<\/script></body></html>'
    );
}

Pebble.addEventListener("showConfiguration", function () {
  Pebble.openURL(configHtml("guai"));
});

Pebble.addEventListener("webviewclosed", function (e) {
  if (e.response && e.response !== "CANCELLED") {
    var opts = JSON.parse(decodeURIComponent(e.response));
    if (opts.srv) localStorage.setItem(SERVER_KEY, opts.srv.trim());
    if (opts.tok) localStorage.setItem(TOKEN_KEY, opts.tok.trim());
    upload();
  }
});
