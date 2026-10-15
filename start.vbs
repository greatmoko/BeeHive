Option Explicit
Dim shell, files, folder, python, quote
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
python = folder & "\.venv\Scripts\pythonw.exe"
If Not files.FileExists(python) Then
    MsgBox "Missing .venv\Scripts\pythonw.exe. Please follow README.md to set up the environment.", vbExclamation, "BeeHive"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
quote = Chr(34)
shell.Run quote & python & quote & " -B " & quote & folder & "\main.py" & quote, 0, False
