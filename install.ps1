# 本の店舗在庫チェック bot（ブック探偵）のセットアップ。setup.bat から起動される。
# このフォルダを丸ごと「買取スキャナー」フォルダの中（買取スキャナー\sedori）へ移動する。
# 買取スキャナーには他のツールが入っているので、その直下にファイルをばらまかないこと。
param([string]$Dest = "")

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Parent = "買取スキャナー"     # 入れ先の親フォルダ名（ユーザーの既存フォルダ）
$Sub = "sedori"                # その中に作る bot のフォルダ名
$Name = "ブック探偵"            # デスクトップのボタン名
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Sleep -Seconds 1   # setup.bat が終了してフォルダのロックが外れるのを待つ
Set-Location $env:TEMP    # 自分の作業フォルダが移動元の中にあると Move-Item が失敗するため外へ出る

function Say($t) { Write-Host $t }

# ---- 1. 移動先を決める -------------------------------------------------------
$desktop = [Environment]::GetFolderPath("Desktop")
$documents = [Environment]::GetFolderPath("MyDocuments")
if (-not $Dest) {
    $parentDir = $null
    foreach ($base in @($desktop, (Join-Path $env:USERPROFILE "OneDrive\デスクトップ"), (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
                        (Join-Path $env:USERPROFILE "Desktop"), $documents, $env:USERPROFILE)) {
        $cand = Join-Path $base $Parent
        if (Test-Path $cand) { $parentDir = $cand; break }
    }
    if (-not $parentDir) { $parentDir = Join-Path $desktop $Parent }
    $Dest = Join-Path $parentDir $Sub
}
$Dest = [System.IO.Path]::GetFullPath($Dest)

Say "=== $Name セットアップ ==="
Say "現在の場所: $Src"
Say "移動先    : $Dest"
Say ""

# ---- 2. フォルダを移動 -------------------------------------------------------
if ($Dest -eq $Src) {
    Say "[1/4] すでに移動先にあります。移動は省略します。"
} else {
    Say "[1/4] bot が動いていれば止めます..."
    Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -like "*bot.main*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500

    if (Test-Path $Dest) {
        # 既存の bot フォルダ（買取スキャナー\sedori）がある: 中身を上書きコピーして元を消す
        # （.env / data は移動先に既にあればそちらを残す）。親の 買取スキャナー 直下には何も置かない。
        if (-not (Test-Path (Join-Path $Dest "bot\main.py"))) {
            Say "      $Dest は bot のフォルダではないようです。中身を混ぜないよう中止します。"
            Read-Host "Enter で閉じます"
            exit 1
        }
        Say "      既存の bot フォルダに中身を入れます..."
        Get-ChildItem -LiteralPath $Src -Force | ForEach-Object {
            $target = Join-Path $Dest $_.Name
            if ($_.Name -in @(".env", "data") -and (Test-Path $target)) { return }
            if ($_.Name -eq ".venv") { return }
            if ($_.PSIsContainer) {
                Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force
            } else {
                Copy-Item -LiteralPath $_.FullName -Destination $target -Force
            }
        }
        Remove-Item -LiteralPath $Src -Recurse -Force
    } else {
        New-Item -ItemType Directory -Path (Split-Path -Parent $Dest) -Force | Out-Null
        Move-Item -LiteralPath $Src -Destination $Dest
    }
    Say "      移動しました。"
}
Set-Location -LiteralPath $Dest

# ---- 3. 仮想環境を作り直す（移動すると古い .venv は使えない） -------------------
Say "[2/4] Python の環境を準備しています（数分かかることがあります）..."
if (Test-Path (Join-Path $Dest ".venv")) { Remove-Item -LiteralPath (Join-Path $Dest ".venv") -Recurse -Force }
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Say "      Python が見つかりません。python.org から 3.10 以上を入れて（Add python.exe to PATH にチェック）、setup.bat をもう一度実行してください。"
    Read-Host "Enter で閉じます"
    exit 1
}
& $py.Source -m venv .venv
& ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Say "      ライブラリの取得に失敗しました。インターネット接続を確認して setup.bat をもう一度。"
    Read-Host "Enter で閉じます"
    exit 1
}
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Say "      .env を作りました。あとで DISCORD_TOKEN= の右にトークンを貼ってください（メモ帳が開きます）。"
    Start-Process notepad ".env"
}
if (-not (Test-Path "data")) { New-Item -ItemType Directory -Path "data" | Out-Null }

# ---- 3b. git の作業フォルダにする（git があれば）-----------------------------------
# Claude Code の会話をこのフォルダに引き継ぐ（claude --teleport）には GitHub と同じブランチの git フォルダが要る。
# .env / .venv / data は .gitignore 済みなので消えない。
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git -and -not (Test-Path ".git")) {
    Say "      git の作業フォルダにします（会話の引き継ぎ用）..."
    $br = "claude/discord-book-inventory-bot-tm2q4h"
    & git init -q
    & git remote add origin "https://github.com/Masatsugu-Miura/sedori.git"
    & git fetch -q origin $br
    if ($LASTEXITCODE -eq 0) {
        & git symbolic-ref HEAD "refs/heads/$br"
        & git reset -q "origin/$br"
        & git branch -q -u "origin/$br"
    } else {
        Say "      （GitHub から取れなかったので省略。bot の動作には影響しません）"
    }
}

# ---- 4. デスクトップのボタン -------------------------------------------------
Say "[3/4] デスクトップに起動ボタンを作ります..."
$shell = New-Object -ComObject WScript.Shell
function Make-Shortcut($path, $target, $arguments, $desc, $icon) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $target
    if ($arguments) { $s.Arguments = $arguments }
    $s.WorkingDirectory = $Dest
    $s.Description = $desc
    if ($icon) { $s.IconLocation = $icon }
    $s.Save()
}
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
Make-Shortcut (Join-Path $desktop "$Name 起動.lnk") $wscript "`"$Dest\start_hidden.vbs`" notify" "本の店舗在庫チェック bot を起動（画面なし）" "$env:SystemRoot\System32\shell32.dll,137"
Make-Shortcut (Join-Path $desktop "$Name 停止.lnk") (Join-Path $Dest "stop.bat") "" "bot を停止" "$env:SystemRoot\System32\shell32.dll,131"
Make-Shortcut (Join-Path $desktop "$Name フォルダ.lnk") $Dest "" "設定ファイルやログのあるフォルダを開く" "$env:SystemRoot\System32\shell32.dll,3"

# ---- 5. 自動起動の登録があれば新しい場所に向け直す --------------------------------
Say "[4/4] 自動起動の登録を確認..."
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "sedori-bot.lnk"
if (Test-Path $startup) {
    Make-Shortcut $startup $wscript "`"$Dest\start_hidden.vbs`"" "ブック探偵 自動起動" $null
    Say "      自動起動の登録を新しい場所に更新しました。"
} else {
    Say "      自動起動は未登録です（登録するなら autostart.bat）。"
}

Say ""
Say "完了しました。"
Say "  フォルダ: $Dest"
Say "  デスクトップの「$Name 起動」をダブルクリックすると bot が動きます（画面は出ません）。"
Say "  止めるときは「$Name 停止」。ログは data\bot.log にあります。"
Read-Host "Enter で閉じます"
