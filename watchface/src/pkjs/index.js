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
var SLOTS_KEY = "GUAI_SLOTS";
var THEME_KEY = "GUAI_THEME";
var GOAL_KEY = "GUAI_GOAL";

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

// Shared XHR with a JS-side timeout guard: pypkjs does not reliably fire
// onerror/ontimeout when the server is unreachable (connection refused hangs), so a
// pure-JS timer guarantees the callback runs exactly once.
function httpX(method, url, token, body, done, timeoutMs) {
  var finished = false;
  var timer = setTimeout(function () { complete(0, ""); }, timeoutMs || 15000);
  function complete(status, text) {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    done(status, text);
  }
  var x = new XMLHttpRequest();
  x.open(method, url, true);
  x.setRequestHeader("Content-Type", "application/json");
  if (token) x.setRequestHeader("Authorization", "Bearer " + token);
  x.onload = function () { complete(x.status, x.responseText); };
  x.onerror = function () { complete(0, ""); };
  x.ontimeout = function () { complete(0, ""); };
  x.send(body ? JSON.stringify(body) : null);
}

function httpPost(url, token, body, done) {
  httpX("POST", url, token, body, function (status) { done(status); }, 15000);
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

function enqueueHeartRate(ts, bpm) {
  if (typeof bpm !== "number" || bpm < 20 || bpm > 250) return;
  enqueue({
    type: "heart_rate",
    dedupeKey: ts,
    body: {
      heart_rates: [{ at: utcIso(ts), bpm: bpm }]
    }
  });
}

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
    // The native watch includes the current HR in its activity snapshot.
    enqueueHeartRate(ts, bpm);
  } else if (p.TYPE === "heart_rate") {
    enqueueHeartRate(ts, bpm);
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

// --- Bottom panel slot assignment (steps / sleep / active / weather) --------

function readSlots() {
  try {
    var s = JSON.parse(localStorage.getItem(SLOTS_KEY) || "[0,1,2]");
    if (!Array.isArray(s) || s.length !== 3) return [0, 1, 2];
    return s.map(function (v) {
      v = parseInt(v, 10);
      return (v >= 0 && v <= 3) ? v : 0;
    });
  } catch (e) {
    return [0, 1, 2];
  }
}

function sendSlots(slots) {
  Pebble.sendAppMessage({
    PANEL_SLOT_A: slots[0], PANEL_SLOT_B: slots[1], PANEL_SLOT_C: slots[2]
  }, function () {}, function () { console.log("pkjs: slots send failed"); });
}

function readTheme() {
  var t = parseInt(localStorage.getItem(THEME_KEY) || "0", 10);
  return (t === 1) ? 1 : 0;
}

function sendTheme(theme) {
  Pebble.sendAppMessage({ UI_THEME: theme }, function () {}, function () {
    console.log("pkjs: theme send failed");
  });
}

function readGoal() {
  var g = parseInt(localStorage.getItem(GOAL_KEY) || "10000", 10);
  return clampGoal(g);
}

function clampGoal(g) {
  if (isNaN(g)) return 10000;
  if (g < 1000) return 1000;
  if (g > 100000) return 100000;
  return g;
}

function sendGoal(goal) {
  Pebble.sendAppMessage({ STEP_GOAL: goal }, function () {}, function () {
    console.log("pkjs: goal send failed");
  });
}

// --- Weather for the watchface row (server data only) -----------------------

var WX_CODES = {
  "clear": 0, "partly_cloudy": 1, "cloudy": 2, "drizzle": 3,
  "rain": 4, "snow": 5, "thunderstorm": 6, "fog": 7
};
var WX_MISSING = -9999; // sentinel: high/low not available today

function sendWxUnavailable() {
  console.log("pkjs: weather unavailable");
  Pebble.sendAppMessage({ WEATHER_COND: 8 }, function () {}, function () {
    console.log("pkjs: weather send failed");
  });
}

function pollWeather() {
  if (!settingsReady()) { sendWxUnavailable(); return; }
  var server = localStorage.getItem(SERVER_KEY).replace(/\/$/, "");
  var token = localStorage.getItem(TOKEN_KEY);
  httpX("GET", server + "/api/weather/current", token, null, function (status, text) {
    if (status !== 200) { sendWxUnavailable(); return; }
    var body;
    try { body = JSON.parse(text); } catch (e) { sendWxUnavailable(); return; }
    var w = body.weather;
    if (!w || typeof w.temp_c !== "number" || !w.condition) { sendWxUnavailable(); return; }
    var payload = {
      WEATHER_TEMP_C: Math.round(w.temp_c * 10),
      WEATHER_COND: (WX_CODES[w.condition] !== undefined) ? WX_CODES[w.condition] : 8,
      WEATHER_TEMP_HIGH_C: (typeof w.temp_high_c === "number") ? Math.round(w.temp_high_c * 10) : WX_MISSING,
      WEATHER_TEMP_LOW_C: (typeof w.temp_low_c === "number") ? Math.round(w.temp_low_c * 10) : WX_MISSING
    };
    console.log("pkjs: weather temp=" + w.temp_c + " cond=" + w.condition);
    Pebble.sendAppMessage(payload, function () {}, function () {
      console.log("pkjs: weather send failed");
    });
  }, 10000);
}

Pebble.addEventListener("ready", function () {
  console.log("pkjs: ready, server=" + localStorage.getItem(SERVER_KEY) + " tokenSet=" + !!localStorage.getItem(TOKEN_KEY));
  sendTheme(readTheme());
  sendSlots(readSlots());
  sendGoal(readGoal());
  upload();
  requestSleepOnce();
  pollWeather();
  // Periodic flush while the watchface is open.
  setInterval(upload, 60000);
  setInterval(pollWeather, 1800000); // weather changes slowly; 30 min
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
  var slots = readSlots();
  var names = ["Steps", "Sleep", "Active", "Weather"];
  // Chip rows instead of <select>: minimal webviews render the select's native
  // arrow with a missing-glyph box. Plain buttons render everywhere.
  var chips = function (id, values, labels, current) {
    return '<span id="' + id + '">' + values.map(function (v, i) {
      return '<button type="button" class="chip' + (current === v ? ' sel' : '') +
        '" data-v="' + v + '">' + labels[i] + '</button>';
    }).join("") + '</span>';
  };
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
      '.chiprow{margin:4px 0 10px}' +
      '.chip{display:inline-block;margin:2px 8px 2px 0;padding:8px 14px;font-size:16px;background:#ffffff;color:#111111;border:1px solid #999;border-radius:6px}' +
      '.chip.sel{background:#36b79f;color:#111111;border-color:#36b79f}' +
      '</style></head>' +
      '<body>' +
      '<h2>GUAI</h2>' +
      '<form id="f">' +
      '<label>Server URL<br><input id="srv" value="' + srv + '"></label><br><br>' +
      '<label>Token<br><input id="tok" value="' + tok + '"></label><br><br>' +
      '<label>Panel slot 1</label><div class="chiprow">' + chips("slot0", [0,1,2,3], names, slots[0]) + '</div>' +
      '<label>Panel slot 2</label><div class="chiprow">' + chips("slot1", [0,1,2,3], names, slots[1]) + '</div>' +
      '<label>Panel slot 3</label><div class="chiprow">' + chips("slot2", [0,1,2,3], names, slots[2]) + '</div>' +
      '<label>Theme</label><div class="chiprow">' + chips("theme", [0,1], ["Dark","Light"], readTheme()) + '</div>' +
      '<label>Step goal (steps/day)<br><input id="goal" type="number" min="1000" max="100000" step="500" value="' + readGoal() + '"></label><br><br>' +
      '<button type="submit">Save</button></form>' +
      '<script>' +
      'var state={slot0:' + slots[0] + ',slot1:' + slots[1] + ',slot2:' + slots[2] + ',theme:' + readTheme() + '};' +
      '["slot0","slot1","slot2","theme"].forEach(function(id){' +
      'var bs=document.getElementById(id).getElementsByTagName("button");' +
      'for(var i=0;i<bs.length;i++){bs[i].onclick=function(){' +
      'for(var j=0;j<bs.length;j++)bs[j].className="chip";' +
      'this.className="chip sel";state[id]=parseInt(this.getAttribute("data-v"),10);}}});' +
      'document.getElementById("f").onsubmit=function(){' +
      'var send=function(){var opts={srv:document.getElementById("srv").value,' +
      'tok:document.getElementById("tok").value,' +
      'slots:[state.slot0,state.slot1,state.slot2],' +
      'theme:state.theme,' +
      'goal:parseInt(document.getElementById("goal").value,10)};location.href=' +
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
    if (opts.slots) {
      var slots = opts.slots.map(function (v) {
        v = parseInt(v, 10);
        return (v >= 0 && v <= 3) ? v : 0;
      });
      localStorage.setItem(SLOTS_KEY, JSON.stringify(slots));
      sendSlots(slots);
    }
    if (opts.theme !== undefined) {
      var theme = (parseInt(opts.theme, 10) === 1) ? 1 : 0;
      localStorage.setItem(THEME_KEY, String(theme));
      sendTheme(theme);
    }
    if (opts.goal !== undefined) {
      var goal = clampGoal(parseInt(opts.goal, 10));
      localStorage.setItem(GOAL_KEY, String(goal));
      sendGoal(goal);
    }
    upload();
    pollWeather();
  }
});
