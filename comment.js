// ゲストブックページ用スクリプト。
// 入力欄の文字数が100文字を超えたら警告を表示し、フォーム送信もキャンセルする。
// サーバー側でも同じ制限を行う（プログレッシブエンハンスメント）。

// <strong> 要素はエラーメッセージ表示に使う。
var strong = document.querySelectorAll("strong")[0];

// キー入力のたびに文字数をチェックし、超過していれば strong に警告を表示する。
function lengthCheck() {
  var value = this.getAttribute("value");
  if (value.length > 100) {
    strong.innerHTML = "Comment too long!";
  }
}

// すべての input 要素に keydown リスナーを登録する。
var inputs = document.querySelectorAll("input");
for (var i = 0; i < inputs.length; i++) {
  inputs[i].addEventListener("keydown", lengthCheck);
}
