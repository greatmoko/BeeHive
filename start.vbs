Option Explicit
Dim shell, folder, quote
Set shell = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
quote = Chr(34)
shell.Run quote & folder & "\.venv\Scripts\pythonw.exe" & quote & " " & quote & folder & "\main.py" & quote, 0, False
