param([ValidateRange(1, 65535)][int]$Port = 9223)
# Start the Docpanel desktop app with local automation access.
# Close the desktop app normally before running this if it is not already in debug mode.
$ErrorActionPreference = 'Stop'
$viewerExe = 'C:\Program Files\Docpanel\Docpanel-Web-Viewer\docpanel-web-viewer.exe'
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    Write-Output "Local debugging port $Port is already listening. The Python check will verify the app identity."
    return
}
if (Get-Process -Name 'docpanel-web-viewer' -ErrorAction SilentlyContinue) {
    throw 'Close Docpanel WebViewer normally, then run this launcher again. Existing case tabs are not closed automatically.'
}
$previousElectronMode = $env:ELECTRON_RUN_AS_NODE
try {
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    Start-Process -FilePath $viewerExe -ArgumentList "--remote-debugging-port=$Port",'--remote-debugging-address=127.0.0.1' -WindowStyle Hidden
    Write-Output "Started Docpanel WebViewer with local debugging on port $Port."
} finally {
    if ($null -ne $previousElectronMode) { $env:ELECTRON_RUN_AS_NODE = $previousElectronMode }
}
