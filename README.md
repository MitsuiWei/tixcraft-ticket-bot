## 演唱會搶票自動化 (Playwright + Python)

這個專案提供一個以 Playwright 撰寫的練習搶票自動化腳本，支援：
- 倒數與「立即購票」自動點擊
- 依價格區塊選位與票數選取
- 自動勾選條款
- 驗證碼 OCR（Tesseract），失敗時回退人工輸入

### 1. 環境需求
- Windows 10/11（已在 PowerShell 測試）
- Python 3.11+（專案目前在 3.13 可運行）
- 可連外的網路環境

### 2. 安裝步驟
在 PowerShell 逐行執行：

```powershell
cd "C:\Users\2509087\Desktop\演唱會搶票程式"

# 建立並啟用 venv（第一次）
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel

# 安裝依賴
.\.venv\Scripts\python.exe -m pip install playwright pillow pytesseract
.\.venv\Scripts\python.exe -m playwright install chromium
```

#### 安裝 Tesseract（OCR）
1) 下載並安裝 Tesseract（Windows 安裝檔）。
2) 取得安裝路徑，例如：`C:\Program Files\Tesseract-OCR\tesseract.exe`
3) 在執行腳本時以參數或環境變數告知路徑：

```powershell
$env:TESSERACT_EXECUTABLE = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

### 3. 使用方式

腳本檔：`auto_buy.py`

常用參數：
- `--seconds`：倒數秒數（預設 3）。
- `--headless`：無頭模式（不開視窗）。
- `--slowmo`：每個操作的延遲毫秒（預設 0）。
- `--timeout`：動作逾時（毫秒，預設 15000）。
- `--price`：要選位的價位（例如 2880、3680）。
- `--quantity`：票數（例如 2）。
- `--tesseract`：Tesseract 可執行檔路徑（Windows）。

範例（可視化、OCR 開啟）：
```powershell
.\.venv\Scripts\python.exe .\auto_buy.py --seconds 5 --slowmo 150 --price 2880 --quantity 2 --tesseract "$env:TESSERACT_EXECUTABLE"
```

最低延遲（建議加較寬鬆 timeout 避免載入超時）：
```powershell
.\.venv\Scripts\python.exe .\auto_buy.py --seconds 0 --slowmo 0 --timeout 20000 --price 2880 --quantity 2 --tesseract "$env:TESSERACT_EXECUTABLE"
```

無頭模式（背景執行）：
```powershell
.\.venv\Scripts\python.exe .\auto_buy.py --seconds 0 --slowmo 0 --headless --timeout 20000 --price 2880 --quantity 2 --tesseract "$env:TESSERACT_EXECUTABLE"
```

### 4. 常見問題
- 看不到瀏覽器或開啟很慢：
  - 取消 `--headless`，或將 `--timeout` 提高為 20000/30000。
- 終端出現 `No module named 'playwright'`：
  - 重新執行依賴安裝步驟（特別是 `pip install playwright` 與 `playwright install chromium`）。
- OCR 沒有自動填入：
  - 確認已安裝 Tesseract，並以 `--tesseract` 指定路徑或設定 `TESSERACT_EXECUTABLE`。
  - 查看專案目錄是否產生 `_before_captcha.png`、`_captcha_full.png`、`_captcha_crop.png` 以協助除錯。
- 需要微調等待時間：
  - 使用 `--slowmo`、`--timeout`，或提 issue 告知我將固定等待參數化。

### 5. 注意事項
- 本專案針對練習站點（`ticket-training.onrender.com`）流程而寫，請勿用於違反網站條款之用途。
- 若練習站的文案/DOM 更新，可能需要調整選擇器；請回報我以便修正。

---


