# Raise a window above every other window (HWND_TOPMOST) so a screen-region
# capture sees the guest and not whatever host window happens to be on top.
# Pass -Clear to drop the topmost flag again afterwards.
param(
    [string]$Title = 'Reims vGPU',
    [switch]$Clear
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class Raise {
    public delegate bool EnumProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumProc p, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    public static readonly IntPtr TOPMOST = new IntPtr(-1);
    public static readonly IntPtr NOTOPMOST = new IntPtr(-2);

    public static IntPtr FindPrefix(string prefix) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            int n = GetWindowTextLength(h);
            if (n == 0) return true;
            var sb = new StringBuilder(n + 1);
            GetWindowText(h, sb, sb.Capacity);
            if (sb.ToString().StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) {
                found = h;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@

$h = [Raise]::FindPrefix($Title)
if ($h -eq [IntPtr]::Zero) {
    "window '$Title' not found"
    exit 1
}
$SWP = 0x0001 -bor 0x0002 -bor 0x0040   # NOSIZE | NOMOVE | SHOWWINDOW
if ($Clear) {
    [Raise]::SetWindowPos($h, [Raise]::NOTOPMOST, 0, 0, 0, 0, $SWP) | Out-Null
    "cleared topmost on '$Title'"
} else {
    [Raise]::ShowWindow($h, 9) | Out-Null                    # SW_RESTORE
    [Raise]::SetWindowPos($h, [Raise]::TOPMOST, 0, 0, 0, 0, $SWP) | Out-Null
    [Raise]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Milliseconds 900
    $fg = [Raise]::GetForegroundWindow()
    "raised '$Title' hwnd=$h topmost; foreground_match=$($fg -eq $h)"
}
