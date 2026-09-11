param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

& python "$PSScriptRoot\main.py" @ScriptArgs
