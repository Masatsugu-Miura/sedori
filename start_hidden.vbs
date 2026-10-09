' Start the bot silently (no window). Log goes to data\bot.log. Use stop.bat to stop it.
' Argument "notify" shows a small message box with the result (used by the desktop button).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
notify = False
If WScript.Arguments.Count > 0 Then
  If LCase(WScript.Arguments(0)) = "notify" Then notify = True
End If
If Not fso.FolderExists(dir & "\data") Then fso.CreateFolder(dir & "\data")
py = dir & "\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then
  ' no venv yet: run the visible installer instead
  sh.Run """" & dir & "\start.bat""", 1, False
  WScript.Quit 0
End If
If Not fso.FileExists(dir & "\.env") Then
  MsgBox ".env がありません。setup.bat か start.bat を一度実行してトークンを設定してください。", 48, "買取スキャナー"
  WScript.Quit 1
End If
' already running? (look for a python process running bot.main)
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT CommandLine FROM Win32_Process WHERE Name = 'python.exe'")
For Each p In procs
  If Not IsNull(p.CommandLine) Then
    If InStr(p.CommandLine, "bot.main") > 0 Then
      If notify Then MsgBox "すでに起動しています。Discord でそのまま使えます。", 64, "買取スキャナー"
      WScript.Quit 0
    End If
  End If
Next
cmd = "cmd /c """"" & py & """ -m bot.main >> """ & dir & "\data\bot.log"" 2>&1"""
sh.Run cmd, 0, False
If notify Then
  WScript.Sleep 4000
  running = False
  Set procs = wmi.ExecQuery("SELECT CommandLine FROM Win32_Process WHERE Name = 'python.exe'")
  For Each p In procs
    If Not IsNull(p.CommandLine) Then
      If InStr(p.CommandLine, "bot.main") > 0 Then running = True
    End If
  Next
  If running Then
    MsgBox "起動しました。Discord に本のコードを貼ると在庫を調べます。" & vbCrLf & "止めるときはデスクトップの「買取スキャナー 停止」。", 64, "買取スキャナー"
  Else
    MsgBox "起動に失敗したようです。data\bot.log を確認してください（トークン未設定が多いです）。", 48, "買取スキャナー"
  End If
End If
