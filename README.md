# BoD 日経ニュース収集

GoogleニュースRSSに掲載された日経の日本語見出しを取得し、既存のCloudflare Workerへ渡します。Workerが金利・国債・為替・中央銀行・経済指標などを分類し、Discordのマーケットニュースチャンネルへ配信します。記事本文やAI要約は取得しません。

## 自動実行

- `.github/workflows/nikkei-collect.yml`：5分間隔のスケジュール（UTCの2、7、12、17…分）。
- 初回は掲載済み記事を記録し、過去記事を一括投稿しません。
- 以降の新着・見出しの更新はWorkerのD1で重複を判定し、既存の2分間隔の配信処理へ送ります。
- Googleの掲載遅延、GitHubの実行待ち、Discordの送信待ちがあります。5分ごとの実行や数分以内の配信は保証されません。
- 公開リポジトリの標準GitHubホストランナーを使用します。有料ランナー、AI API、有料ニュースAPIは使用しません。

## 認証

このリポジトリのmainブランチ上にある指定Workflowだけが、GitHubの短時間のOIDCトークンでRSSを送信できます。Workerは署名、対象URL、リポジトリID、所有者ID、ブランチ、Workflow、期限を検証します。Discord Botトークン・Cloudflare APIトークンをこのリポジトリやGitHub Secretsへ保存する必要はありません。

## 確認

GitHubの **Actions → Nikkei Japanese market news** で緑の成功マークとログを確認します。`google_http_status: 200` と `worker.ok: true` が取得・受け渡し成功です。`matched` は金融関連の件数、`queued` はその回に送信待ちへ登録した件数です。同じ記事しかなければ`queued: 0`が正常です。

手動取得は **Run workflow** から実行します。`Post one clearly labelled test card to Discord` を選ぶと、対象の最新記事を1件だけ【テスト】付きで投稿します。通常は選ばないでください。同じ記事のテストを再実行すると元のテスト投稿を更新します。

Workerの管理画面のニュース稼働状況では、`nikkei_collector: github`、`last_success_jst`、`last_error`、`info.received_at_jst` を確認できます。日経のプレビューはGitHubから最後に受け取ったRSSを利用します。

## 停止と復旧

- 日経だけを停止：Cloudflare Workerの変数 `NEWS_NIKKEI_ENABLED` を `false` にして反映。
- 全市場ニュースを停止：`NEWS_ENABLED=false`。
- 収集自体を停止：ActionsのこのWorkflowを **Disable workflow**。
- 再開：スイッチを戻しWorkflowを有効化。投稿済み履歴は削除しないでください。
- 公開リポジトリの定期実行は、リポジトリで60日間活動がないと無効になることがあります。自動実行が止まった場合はActionsで再有効化してください。
- Google RSSの取得エラー時はその回の処理を失敗として記録し、記事0件として基準を更新しません。

## リンク

通知のリンクはGoogleニュースRSSにある記事リンクを使います。Googleニュースを経由して日経の記事へ移動します。Workerは有料記事の本文や認証を取得しません。

## 導入時の検証

2026年10月11日 04:16 JST、Google RSS HTTP 200、日経日本語47件、Workerの金融関連3件を初期登録（過去記事の投稿0件）。Workerの既存機能と連携のローカルテスト129件が通過しました。
