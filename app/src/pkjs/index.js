/**
 * GUAI Check-In app — PebbleKit JS.
 *
 * Pulls pending check-ins from the server (GET /api/checkins/pending), hands them
 * to C one at a time (AppMessage), and submits the answered values
 * (POST /api/checkins/instances/{id}/responses). HTTP uses XMLHttpRequest
 * (fetch is not available in PebbleKit JS/pypkjs).
 *
 * Configuration (server URL + token) is set via the app's config page and stored
 * in localStorage — same keys as the watchface (separate per-app storage).
 */

var SERVER_KEY = "GUAI_SERVER";
var TOKEN_KEY = "GUAI_TOKEN";
var RETRY_KEY = "GUAI_RETRY";

var pendingQueue = [];
var retryQueue = [];

function settingsReady() {
  return !!localStorage.getItem(SERVER_KEY) && !!localStorage.getItem(TOKEN_KEY);
}

function loadRetry() {
  try { retryQueue = JSON.parse(localStorage.getItem(RETRY_KEY) || "[]"); }
  catch (e) { retryQueue = []; }
}
function saveRetry() { localStorage.setItem(RETRY_KEY, JSON.stringify(retryQueue.slice(-50))); }

function sendAppMessage(payload) {
  Pebble.sendAppMessage(payload, function () {}, function () {
    console.log("pkjs: send failed");
  });
}

function sendStatus(text) {
  sendAppMessage({ TYPE: "status", STATUS_TEXT: text });
}

// --- HTTP (XHR) -------------------------------------------------------------

function xhr(method, url, token, body, done) {
  var xhr = new XMLHttpRequest();
  xhr.open(method, url, true);
  xhr.setRequestHeader("Content-Type", "application/json");
  if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
  xhr.timeout = 15000;
  xhr.onload = function () { done(xhr.status, xhr.responseText); };
  xhr.onerror = function () { done(0, ""); };
  xhr.ontimeout = function () { done(0, ""); };
  xhr.send(body ? JSON.stringify(body) : null);
}

function getPending(done) {
  if (!settingsReady()) { done(null); return; }
  var server = localStorage.getItem(SERVER_KEY).replace(/\/$/, "");
  var token = localStorage.getItem(TOKEN_KEY);
  xhr("GET", server + "/api/checkins/pending", token, null, function (status, text) {
    if (status !== 200) { console.log("pkjs: pending fetch status=" + status); done(null); return; }
    try { done(JSON.parse(text)); }
    catch (e) { done(null); }
  });
}

function submitAnswer(instanceId, answerType, value) {
  var server = localStorage.getItem(SERVER_KEY).replace(/\/$/, "");
  var token = localStorage.getItem(TOKEN_KEY);
  var body = {};
  if (answerType === "boolean") body.boolean = value === 1;
  else if (answerType === "scale") body.scale = value;
  else body.choice = value;

  var record = { instance_id: instanceId, answer_type: answerType, value: value, attempts: 0 };
  loadRetry();
  retryQueue = retryQueue.filter(function (r) { return r.instance_id !== instanceId; });
  retryQueue.unshift(record);

  xhr("POST", server + "/api/checkins/instances/" + instanceId + "/responses", token, body, function (status) {
    loadRetry();
    if (status === 200) {
      retryQueue = retryQueue.filter(function (r) { return r.instance_id !== instanceId; });
      saveRetry();
      sendStatus("ok");
    } else if (status === 409) {
      retryQueue = retryQueue.filter(function (r) { return r.instance_id !== instanceId; });
      saveRetry();
      sendStatus("already");
    } else if (status === 410) {
      retryQueue = retryQueue.filter(function (r) { return r.instance_id !== instanceId; });
      saveRetry();
      sendStatus("too_late");
    } else {
      // offline / 5xx / 422 / 401: keep in the retry queue and tell C to show feedback
      var rec = retryQueue.filter(function (r) { return r.instance_id === instanceId; })[0];
      if (rec) rec.attempts += 1;
      saveRetry();
      sendStatus("error");
    }
  });
}

// --- Question queue ---------------------------------------------------------

function refillQueue() {
  getPending(function (list) {
    if (list === null) { sendStatus("error"); return; }
    pendingQueue = list;
    if (pendingQueue.length === 0) {
      sendAppMessage({ REQUEST: "idle" });
    } else {
      askNext();
    }
  });
}

function removeAnswered(instanceId) {
  pendingQueue = pendingQueue.filter(function (p) { return p.instance_id !== instanceId; });
}

function askNext() {
  // retry queue first — an unanswered submission takes priority (bounded retries:
  // after 5 failures the instance stays pending on the server for a later session)
  if (retryQueue.length > 0 && retryQueue[0].attempts > 0 && retryQueue[0].attempts <= 5) {
    var rec = retryQueue[0];
    submitAnswer(rec.instance_id, rec.answer_type, rec.value);  // re-submits
    return;
  }
  retryQueue = retryQueue.filter(function (r) { return r.attempts <= 5; });
  saveRetry();
  if (pendingQueue.length === 0) { refillQueue(); return; }
  var item = pendingQueue[0];
  sendAppMessage({
    TYPE: "question",
    INSTANCE_ID: item.instance_id,
    QUESTION: item.question,
    ANSWER_TYPE: item.answer_type,
    ANSWER_OPTIONS: (item.options || []).join("\n"),
    SCALE_MIN: item.scale ? item.scale.min : 0,
    SCALE_MAX: item.scale ? item.scale.max : 10,
    DUE_BY: 0
  });
  console.log("pkjs: sent question " + item.question + " (" + item.answer_type + ")");
}

function onAnswer(payload) {
  var instanceId = payload.INSTANCE_ID;
  var answerType = payload.ANSWER_TYPE;
  var value;
  if (payload.ANSWER_CHOICE !== undefined && payload.ANSWER_CHOICE !== null) {
    answerType = "single_choice";
    value = payload.ANSWER_CHOICE;
  } else {
    value = payload.ANSWER_VALUE;
  }
  // fall back to the type of the question we last handed to C
  if (!answerType && pendingQueue[0]) answerType = pendingQueue[0].answer_type;
  if (!answerType) answerType = "boolean";
  removeAnswered(instanceId);
  submitAnswer(instanceId, answerType, value);
}

// --- Events -----------------------------------------------------------------

Pebble.addEventListener("appmessage", function (e) {
  var p = e.payload;
  if (p.TYPE === "answer") {
    onAnswer(p);
  } else if (p.REQUEST === "next") {
    askNext();
  }
});

Pebble.addEventListener("ready", function () {
  console.log("pkjs: ready, server=" + localStorage.getItem(SERVER_KEY) + " tokenSet=" + !!localStorage.getItem(TOKEN_KEY));
  loadRetry();
  refillQueue();
  // Periodic refresh while the app stays open (a newly due check-in appears).
  setInterval(function () {
    if (pendingQueue.length === 0) refillQueue();
  }, 30000);
});

// --- Configuration page (self-contained) --------------------------------------

function configHtml() {
  var srv = encodeURIComponent(localStorage.getItem(SERVER_KEY) || "");
  var tok = encodeURIComponent(localStorage.getItem(TOKEN_KEY) || "");
  return "data:text/html;charset=utf-8," +
    encodeURIComponent(
      '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:16px">' +
      '<h2>GUAI Check-In</h2>' +
      '<form id="f">' +
      '<label>Server URL<br><input id="srv" style="width:100%" value="' + srv + '"></label><br><br>' +
      '<label>Token<br><input id="tok" style="width:100%" value="' + tok + '"></label><br><br>' +
      '<button type="submit">Save</button></form>' +
      '<script>document.getElementById("f").onsubmit=function(){' +
      'var send=function(){var opts={srv:document.getElementById("srv").value,' +
      'tok:document.getElementById("tok").value};location.href=' +
      '"pebblejs://close#"+encodeURIComponent(JSON.stringify(opts))};' +
      'setTimeout(send,100);return false;};<\/script></body></html>'
    );
}

Pebble.addEventListener("showConfiguration", function () {
  Pebble.openURL(configHtml());
});

Pebble.addEventListener("webviewclosed", function (e) {
  if (e.response && e.response !== "CANCELLED") {
    var opts = JSON.parse(decodeURIComponent(e.response));
    if (opts.srv) localStorage.setItem(SERVER_KEY, opts.srv.trim());
    if (opts.tok) localStorage.setItem(TOKEN_KEY, opts.tok.trim());
    refillQueue();
  }
});