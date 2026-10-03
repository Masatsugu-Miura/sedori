' Start the bot silently (no window). Log goes to data\bot.log. Use stop.bat to stop it.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
If Not fso.FolderExists(dir & "\data") Then fso.CreateFolder(dir & "\data")
py = dir & "\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then
  MsgBox "Run start.bat once first (it creates .venv).", 48, "sedori bot"
  WScript.Quit 1
End If
' already running? (look for a python process running bot.main)
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT CommandLine FROM Win32_Process WHERE Name = 'python.exe'")
For Each p In procs
  If Not IsNull(p.CommandLine) Then
    If InStr(p.CommandLine, "bot.main") > 0 Then
      WScript.Quit 0
    End If
  End If
Next
cmd = "cmd /c """"" & py & """ -m bot.main >> """ & dir & "\data\bot.log"" 2>&1"""
sh.Run cmd, 0, False
