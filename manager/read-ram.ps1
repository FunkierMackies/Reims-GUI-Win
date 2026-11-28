$d = Get-CimInstance Win32_PhysicalMemory | Sort-Object DeviceLocator
foreach ($m in $d) {
    $v = @(
        ('Manufacturer=' + $m.Manufacturer),
        ('SerialNumber=' + $m.SerialNumber),
        ('PartNumber=' + $m.PartNumber),
        ('DeviceLocator=' + $m.DeviceLocator),
        ('BankLabel=' + $m.BankLabel),
        ('ConfiguredClockSpeed=' + $m.ConfiguredClockSpeed),
        ('SMBIOSMemoryType=' + $m.SMBIOSMemoryType),
        ('Capacity=' + $m.Capacity)
    )
    [Console]::WriteLine(($v -join '|@|'))
}
