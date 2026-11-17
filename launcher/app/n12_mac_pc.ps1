# n12_mac_pc.ps1 v3.152 — зеркало MAC на компьютере. Запускается лаунчером
# с правами администратора (окно UAC). Адаптер выбирается по IP компьютера
# в сети платы. Результат — одной строкой в файл __OUT__.
$ErrorActionPreference = 'Stop'
$out = '__OUT__'
try {
  $ipa = Get-NetIPAddress -AddressFamily IPv4 -IPAddress '__PCIP__' | Select-Object -First 1
  $ad = Get-NetAdapter -InterfaceIndex $ipa.InterfaceIndex
  $old = $ad.MacAddress
  if ('__MODE__' -eq 'restore') {
    Reset-NetAdapterAdvancedProperty -Name $ad.Name -RegistryKeyword 'NetworkAddress' -NoRestart -ErrorAction SilentlyContinue
    Restart-NetAdapter -Name $ad.Name -Confirm:$false
  } else {
    Set-NetAdapter -Name $ad.Name -MacAddress '__MAC__' -Confirm:$false
  }
  Start-Sleep -Seconds 4
  $now = (Get-NetAdapter -Name $ad.Name).MacAddress
  "OK|$($ad.Name)|$old|$now" | Out-File -FilePath $out -Encoding utf8
} catch {
  "ERR|$($_.Exception.Message)" | Out-File -FilePath $out -Encoding utf8
}
