// One desktop window owns the web UI, gateway and Renode lifecycle.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    internal const string Title = "STM32 Sensor Lab";
    internal static readonly string Root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
    internal static readonly int ActivateMessage = RegisterWindowMessage("SensorLab-" + Identity());

    private static string Identity()
    {
        using (var hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Root.ToUpperInvariant()))).Replace("-", "");
    }

    [STAThread]
    private static void Main()
    {
        bool first;
        using (var mutex = new Mutex(true, @"Local\SensorLab-" + Identity(), out first))
        {
            if (!first)
            {
                // The original window may still be initializing its controls.
                for (int n = 0; n < 20; n++)
                {
                    IntPtr handle = FindWindow(null, Title);
                    if (handle != IntPtr.Zero) { PostMessage(handle, ActivateMessage, IntPtr.Zero, IntPtr.Zero); return; }
                    Thread.Sleep(100);
                }
                return;
            }
            try
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new LabWindow());
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int RegisterWindowMessage(string message);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr FindWindow(string name, string title);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr window, int message, IntPtr wparam, IntPtr lparam);
}

internal sealed class LabWindow : Form
{
    private const string Origin = "http://127.0.0.1:8765";
    private readonly WebView2 web = new WebView2 { Dock = DockStyle.Fill, Visible = false, DefaultBackgroundColor = Color.FromArgb(13, 21, 30) };
    private readonly Panel loading = new Panel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(13, 21, 30) };
    private readonly Label message = new Label { AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, Dock = DockStyle.Fill };
    private readonly Button retry = new Button { Text = "Retry startup", Dock = DockStyle.Bottom, Height = 48, Visible = false };
    private readonly HttpClient client = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
    private readonly CancellationTokenSource lifetime = new CancellationTokenSource();
    private readonly object logLock = new object();
    private Process backend;
    private ProcessJob job;
    private Task startup;
    private bool closing, closed, ready;
    private readonly System.Windows.Forms.Timer monitor = new System.Windows.Forms.Timer { Interval = 2000 };
    private readonly string logPath = Path.Combine(Program.Root, "var", "desktop", "app.log");

    internal LabWindow()
    {
        Text = Program.Title;
        Size = new Size(1380, 940);
        MinimumSize = new Size(940, 640);
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Segoe UI", 11);
        BackColor = Color.FromArgb(13, 21, 30);
        ForeColor = Color.FromArgb(232, 239, 245);
        retry.BackColor = Color.FromArgb(33, 49, 63);
        retry.ForeColor = ForeColor;
        retry.FlatStyle = FlatStyle.Flat;
        retry.FlatAppearance.BorderColor = Color.FromArgb(82, 103, 119);
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        loading.Controls.Add(message);
        loading.Controls.Add(retry);
        Controls.Add(web);
        Controls.Add(loading);
        Directory.CreateDirectory(Path.GetDirectoryName(logPath));
        Shown += delegate { startup = StartLab(); };
        retry.Click += delegate { retry.Visible = false; startup = StartLab(); };
        FormClosing += OnClosing;
        monitor.Tick += delegate
        {
            if (ready && backend != null && backend.HasExited && !closing)
            {
                ready = false;
                ShowMessage("The gateway stopped. Your saved reports are safe.\n\n" +
                            "Select Retry startup to restart the virtual board.\nLog: " + logPath);
                retry.Visible = true;
            }
        };
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        int dark = 1;
        // Supported Windows versions draw a dark caption; others retain their default.
        DwmSetWindowAttribute(Handle, 20, ref dark, sizeof(int));
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);

    protected override void WndProc(ref Message value)
    {
        if (value.Msg == Program.ActivateMessage)
        {
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Show();
            Activate();
        }
        base.WndProc(ref value);
    }

    private void ShowMessage(string value)
    {
        message.Text = "STM32 SENSOR LAB\n\n" + value;
        loading.Visible = true;
        loading.BringToFront();
    }

    private void Log(string line)
    {
        if (line == null) return;
        lock (logLock)
        {
            try
            {
                if (File.Exists(logPath) && new FileInfo(logPath).Length > 1000000)
                {
                    File.Copy(logPath, logPath + ".1", true);
                    File.WriteAllText(logPath, "");
                }
                File.AppendAllText(logPath, DateTime.UtcNow.ToString("o") + " " + line + Environment.NewLine);
            }
            catch (IOException) { }
        }
    }

    private async Task<Dictionary<string, object>> Status()
    {
        string json = await client.GetStringAsync(Origin + "/api/v1/demo");
        return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
    }

    private static bool IsOurService(Dictionary<string, object> info)
    {
        object id, project;
        return info.TryGetValue("demo_id", out id) && (string)id == "stm32-renode-lab-v2" &&
               info.TryGetValue("project", out project) &&
               String.Equals(Path.GetFullPath((string)project), Program.Root, StringComparison.OrdinalIgnoreCase);
    }

    private async Task<bool> ExistingService()
    {
        using (var probe = new TcpClient())
        {
            try { await probe.ConnectAsync("127.0.0.1", 8765); }
            catch (SocketException) { return false; }
        }
        try
        {
            if (IsOurService(await Status())) return true;
        }
        catch (Exception error) { Log("Port check: " + error.Message); }
        throw new InvalidOperationException("Port 8765 is being used by another application.\nClose that service, then select Retry startup.");
    }

    private void StartBackend()
    {
        string python = Path.Combine(Program.Root, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
            throw new FileNotFoundException("The project's Python environment is missing. See docs/SENSOR-LAB.md.", python);
        job = new ProcessJob();
        backend = new Process
        {
            StartInfo = new ProcessStartInfo(python, "-u scripts/interview_demo.py --no-browser --desktop-control")
            {
                WorkingDirectory = Program.Root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true
            }
        };
        backend.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log(e.Data); };
        backend.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log(e.Data); };
        backend.Start();
        try
        {
            // Python waits for "start", so no Renode child can escape job ownership.
            job.Assign(backend);
            backend.BeginOutputReadLine();
            backend.BeginErrorReadLine();
            backend.StandardInput.WriteLine("start");
            backend.StandardInput.Flush();
            Log("Owned gateway PID " + backend.Id);
        }
        catch
        {
            if (!backend.HasExited) backend.Kill();
            throw;
        }
    }

    private async Task StartLab()
    {
        Exception failure = null;
        try
        {
            ShowMessage("Starting the virtual STM32F401RE board…\n\nRenode • FreeRTOS • MPU6050 • BMP280");
            await StopBackend();
            lifetime.Token.ThrowIfCancellationRequested();
            bool reused = await ExistingService();
            lifetime.Token.ThrowIfCancellationRequested();
            if (!reused) StartBackend();
            else Log("Reusing matching service; it remains running when this window closes.");
            DateTime deadline = DateTime.UtcNow.AddSeconds(90);
            bool healthy = false;
            while (DateTime.UtcNow < deadline)
            {
                lifetime.Token.ThrowIfCancellationRequested();
                if (backend != null && backend.HasExited)
                    throw new InvalidOperationException("The gateway could not start (exit " + backend.ExitCode + ").\nSee " + logPath);
                try
                {
                    var info = await Status();
                    if (!IsOurService(info)) throw new InvalidOperationException("An unexpected service replaced the gateway.");
                    object value;
                    healthy = info.TryGetValue("ready", out value) && value is bool && (bool)value;
                    if (healthy) break;
                }
                catch (HttpRequestException) { }
                catch (TaskCanceledException) { }
                await Task.Delay(250, lifetime.Token);
            }
            if (!healthy) throw new TimeoutException("Firmware preflight did not finish within 90 seconds.\nSee " + logPath);
            if (web.CoreWebView2 == null)
            {
                ShowMessage("Preflight passed. Opening your workspace…");
                var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Program.Root, "var", "desktop", "webview"));
                lifetime.Token.ThrowIfCancellationRequested();
                await web.EnsureCoreWebView2Async(environment);
                web.CoreWebView2.Profile.PreferredColorScheme = CoreWebView2PreferredColorScheme.Dark;
                web.CoreWebView2.Settings.AreDevToolsEnabled = false;
                web.CoreWebView2.Settings.IsStatusBarEnabled = false;
                web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                web.CoreWebView2.Settings.IsZoomControlEnabled = true;
                web.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
                {
                    Uri destination;
                    e.Cancel = !Uri.TryCreate(e.Uri, UriKind.Absolute, out destination) ||
                               destination.GetLeftPart(UriPartial.Authority) != Origin;
                };
                web.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
                web.CoreWebView2.Profile.DefaultDownloadFolderPath = Path.Combine(Program.Root, "var", "desktop", "downloads");
                Directory.CreateDirectory(web.CoreWebView2.Profile.DefaultDownloadFolderPath);
                web.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
                {
                    if (closing) return;
                    // An attachment download can cancel navigation while the workspace stays loaded.
                    if (e.WebErrorStatus == CoreWebView2WebErrorStatus.OperationCanceled) return;
                    if (e.IsSuccess) { loading.Visible = false; web.Visible = true; web.BringToFront(); }
                    else { ShowMessage("The workspace could not load: " + e.WebErrorStatus); retry.Visible = true; }
                };
            }
            lifetime.Token.ThrowIfCancellationRequested();
            web.CoreWebView2.Navigate(Origin + "/lab");
            ready = true;
            monitor.Start();
            Log("Desktop ready" + (reused ? " (existing service)" : ""));
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            Log(error.ToString());
            failure = error;
        }
        if (failure != null)
        {
            await StopBackend();
            if (!closing)
            {
                ShowMessage(failure.Message + "\n\nLogs: " + logPath);
                retry.Visible = true;
            }
        }
    }

    private async Task StopBackend()
    {
        if (backend != null)
        {
            try
            {
                if (!backend.HasExited)
                {
                    backend.StandardInput.WriteLine("shutdown");
                    backend.StandardInput.Flush();
                    bool exited = await Task.Run(() => backend.WaitForExit(30000));
                    if (!exited) Log("Graceful shutdown timed out; closing the owned process job.");
                }
            }
            catch (InvalidOperationException) { }
            catch (IOException) { }
            finally
            {
                if (job != null) { job.Dispose(); job = null; }
                backend.Dispose();
                backend = null;
            }
        }
        if (job != null) { job.Dispose(); job = null; }
    }

    private async void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (closed) return;
        e.Cancel = true;
        if (closing) return;
        closing = true;
        monitor.Stop();
        retry.Visible = false;
        ShowMessage("Saving test results and stopping Renode…");
        lifetime.Cancel();
        if (startup != null) await startup;
        await StopBackend();
        Log("Desktop closed cleanly.");
        web.Dispose();
        client.Dispose();
        lifetime.Dispose();
        monitor.Dispose();
        closed = true;
        Close();
    }
}

internal sealed class ProcessJob : IDisposable
{
    private IntPtr handle;
    internal ProcessJob()
    {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        var info = new ExtendedLimits();
        info.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(handle, 9, ref info, Marshal.SizeOf(info)))
        {
            int error = Marshal.GetLastWin32Error();
            Dispose();
            throw new System.ComponentModel.Win32Exception(error);
        }
    }
    internal void Assign(Process process)
    {
        if (!AssignProcessToJobObject(handle, process.Handle)) throw new System.ComponentModel.Win32Exception();
    }
    public void Dispose()
    {
        if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
    {
        internal long PerProcessTime, PerJobTime;
        internal uint LimitFlags;
        internal UIntPtr MinWorkingSet, MaxWorkingSet;
        internal uint ActiveProcessLimit;
        internal UIntPtr Affinity;
        internal uint Priority, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters { internal ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        internal BasicLimits Basic;
        internal IoCounters Io;
        internal UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimits limits, int size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
}
