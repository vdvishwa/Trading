name: NSE & US 70/30 Cloud AI Auto-Pilot

on:
  schedule:
    # Indian Market Hours: 09:15 to 15:35 IST (03:45 to 10:05 UTC), Mon-Fri (every 5 mins)
    - cron: '*/5 3-10 * * 1-5'
    # US Market Hours: 19:30 to 02:35 IST (14:00 to 21:05 UTC), Mon-Fri (every 15 mins)
    - cron: '*/15 14-21 * * 1-5'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  run-autopilot:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Run 24/7 NSE & US AI Cloud Worker
        run: python ai_cloud_worker.py

      - name: Commit & Push Updated Cloud Ledger
        run: |
          git config --global user.name "github-actions[bot]"
          git config --global user.email "github-actions[bot]@users.noreply.github.com"
          git add cloud_ledger.json
          git diff --staged --quiet || (git commit -m "AI Cloud Ledger Sync [skip ci]" && git pull --rebase origin main && git push)
