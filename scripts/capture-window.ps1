# Capture the reims-vgpu SDL window to a PNG (or the whole screen when the
# window is not found), for the bring-up record.
param(
    [string]$Title = 'reims-vgpu',
    [string]$Out = 'D:\qemu-reims-complete\run\window.png',
    [int]$Wait = 0
)

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class Win {
    public delegate bool EnumProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumProc p, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr h, int cmd);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }

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

if ($Wait -gt 0) { Start-Sleep -Seconds $Wait }
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null

$h = [Win]::FindPrefix($Title)
if ($h -eq [IntPtr]::Zero) {
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
    $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
    "window '$Title' not found; captured the screen: $Out"
} else {
    [Win]::ShowWindow($h, 9) | Out-Null      # SW_RESTORE
    [Win]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Milliseconds 800
    $r = New-Object Win+RECT
    [Win]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.Right - $r.Left
    $hh = $r.Bottom - $r.Top
    $bmp = New-Object System.Drawing.Bitmap $w, $hh
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
    $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
    "captured '$Title' ${w}x${hh} at $($r.Left),$($r.Top): $Out"
}
