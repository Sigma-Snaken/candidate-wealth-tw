#!/usr/bin/env bash
# 部署「看板財產透視」到 Cloud Run（sigma-snaken-dev / asia-east1）。只在使用者確認後手動執行。
# --source . 會用 repo 根目錄的 Dockerfile 經 Cloud Build 建置，image 推到
# Artifact Registry 的 cloud-run-source-deploy repository（asia-east1，第一次部署會詢問是否建立）。
# 上傳內容由 .gcloudignore 白名單決定。
# 組織政策 iam.allowedPolicyMemberDomains 擋住 allUsers IAM 綁定（--allow-unauthenticated 會報 Setting IAM policy failed），
# 公開存取要靠 --no-invoker-iam-check（hotel-linebot 同樣設定 invoker-iam-disabled）。GEMINI_API_KEY 從目前 shell 環境帶入，檔案內不放金鑰。
set -euo pipefail
cd "$(dirname "$0")/.."
: "${GEMINI_API_KEY:?GEMINI_API_KEY 未設定}"
: "${TELEGRAM_BOT_TOKEN:?}" "${TELEGRAM_CHAT_ID:?}" "${BILLBOARD_REPORT_TOKEN:?}"  # 使用量摘要送 Telegram、/api/report 的共享密鑰（dev.env）

gcloud run deploy candidate-billboard --source . --project sigma-snaken-dev --region asia-east1 --allow-unauthenticated --no-invoker-iam-check --memory 1Gi --cpu 1 --concurrency 4 --max-instances 2 --min-instances 0 --timeout 60 --set-env-vars GEMINI_API_KEY="$GEMINI_API_KEY",DAILY_CAP=500,TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN",TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID",BILLBOARD_REPORT_TOKEN="$BILLBOARD_REPORT_TOKEN"
