// ブラウザが提供するJavaScriptランタイム。
// ユーザースクリプトより先に実行され、console / document / Node / Event などの
// グローバルオブジェクトを定義する。

// --- console ---
// call_python("log", ...) を console.log として公開する。
console = {
  log: function (x) {
    call_python("log", x);
  },
};

// --- document ---
// querySelectorAll はCSSセレクタにマッチする Node オブジェクトの配列を返す。
// Python側からはハンドル（整数ID）のリストが返ってくるので Node でラップする。
document = {
  querySelectorAll: function (s) {
    var handles = call_python("querySelectorAll", s);
    return handles.map(function (h) {
      return new Node(h);
    });
  },
};

// --- Node ---
// Python側の Element を直接JSに渡せないため、整数のハンドルで間接参照する。
// ファイルディスクリプタと同じ考え方。
function Node(handle) {
  this.handle = handle;
}

// getAttribute: ハンドルと属性名をPython側に渡して値を取得する。
Node.prototype.getAttribute = function (attr) {
  return call_python("getAttribute", this.handle, attr);
};

// --- イベントリスナー管理 ---
// LISTENERS[handle][type] = [listener, ...] の形で保持する。
LISTENERS = {};

Node.prototype.addEventListener = function (type, listener) {
  if (!LISTENERS[this.handle]) LISTENERS[this.handle] = {};
  var dict = LISTENERS[this.handle];
  if (!dict[type]) dict[type] = [];
  var list = dict[type];
  list.push(listener);
};

// dispatchEvent: 登録済みリスナーを順に呼び出す。
// evt.do_default を返すことで Python側が preventDefault を検知できる。
Node.prototype.dispatchEvent = function (evt) {
  var type = evt.type;
  var handle = this.handle;
  var list = (LISTENERS[handle] && LISTENERS[handle][type]) || [];
  for (var i = 0; i < list.length; i++) {
    list[i].call(this, evt);
  }

  return evt.do_default;
};

// --- innerHTML セッター ---
// Object.defineProperty でプロパティへの代入をフックし、Python側に委譲する。
Object.defineProperty(Node.prototype, "innerHTML", {
  set: function (s) {
    call_python("innerHTML_set", this.handle, s.toString());
  },
});

// --- Event ---
// イベントオブジェクト。do_default フラグで preventDefault を表現する。
function Event(type) {
  this.type = type;
  this.do_default = true;
}

// preventDefault() を呼ぶとブラウザのデフォルト動作（リンク遷移・フォーム送信など）がキャンセルされる。
Event.prototype.preventDefault = function () {
  this.do_default = false;
};

function XMLHttpRequest() {}

XMLHttpRequest.prototype.open = function (method, url, is_async) {
  if (is_async) throw Error("Asynchronous XHR is not supported");
  this.method = method;
  this.url = url;
};

XMLHttpRequest.prototype.send = function (body) {
  this.responseText = call_python(
    "XMLHttpRequest_send",
    this.method,
    this.url,
    body
  );
};
