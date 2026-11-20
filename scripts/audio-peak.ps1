# Sample the Windows default playback device's peak meter.
#
#   powershell -File scripts\audio-peak.ps1 [samples] [delay_ms]
#
# Prints the peak seen (0.0 .. 1.0) and how many samples were non-zero. Run it
# while the guest plays a sound: a non-zero peak means QEMU's audio output is
# reaching the host's default output device (the same one DirectSound uses).
param([int]$Samples = 40, [int]$DelayMs = 75)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumeratorComObject { }

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator
{
    int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice
{
    int Activate(ref Guid iid, int clsCtx, IntPtr activationParams,
                 [MarshalAs(UnmanagedType.IUnknown)] out object iface);
}

[Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioMeterInformation
{
    int GetPeakValue(out float peak);
}

public static class AudioPeak
{
    public static float Sample()
    {
        var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice dev;
        // eRender = 0, eConsole = 0.
        if (en.GetDefaultAudioEndpoint(0, 0, out dev) != 0) return -1f;
        Guid iid = typeof(IAudioMeterInformation).GUID;
        object o;
        // CLSCTX_INPROC_SERVER = 1.
        if (dev.Activate(ref iid, 1, IntPtr.Zero, out o) != 0) return -1f;
        float peak;
        if (((IAudioMeterInformation)o).GetPeakValue(out peak) != 0) return -1f;
        return peak;
    }
}
'@

$max = 0.0
$nonzero = 0
for ($i = 0; $i -lt $Samples; $i++) {
    $p = [AudioPeak]::Sample()
    if ($p -lt 0) { throw "could not read the default playback device's meter" }
    if ($p -gt $max) { $max = $p }
    if ($p -gt 0.0001) { $nonzero++ }
    Start-Sleep -Milliseconds $DelayMs
}
"peak {0:N4}; {1} of {2} samples non-zero" -f $max, $nonzero, $Samples
