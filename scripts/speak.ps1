param([Parameter(Mandatory=$true)][string]$TextPath, [string]$Voice = "")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Voice) {
        $speaker.SelectVoice($Voice)
    } else {
        $chinese = $speaker.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like "zh-*" } | Select-Object -First 1
        if (-not $chinese) { throw "No installed Chinese System.Speech voice" }
        $speaker.SelectVoice($chinese.VoiceInfo.Name)
    }
    $speaker.Speak([System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8))
} finally {
    $speaker.Dispose()
}
