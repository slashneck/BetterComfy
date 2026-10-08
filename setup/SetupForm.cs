using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace BetterComfySetup;

internal sealed class SetupForm : Form
{
    // setup-art.png: 33 frames of 192 px in rows of 13, drawn at 30 fps
    private const int Frames = 33, FrameSize = 192, Columns = 13;

    private readonly SetupOptions _options;
    private readonly float _scale;
    private readonly Bitmap _sprite;
    private readonly Timer _animation = new() { Interval = 33 };
    private readonly Font _small = Theme.Body(8.5f);
    private readonly Label _title = new();
    private readonly Label _subtitle = new();
    private readonly Panel _page = new();
    private Action _primaryAction;
    private int _frame;
    private bool _installing;

    public SetupForm(SetupOptions options)
    {
        _options = options;
        using (var g = Graphics.FromHwnd(IntPtr.Zero)) _scale = g.DpiX / 96f;
        using (var stream = Resource("art.png")) _sprite = new Bitmap(stream);
        using (var stream = Resource("icon.ico")) Icon = new Icon(stream);

        AutoScaleMode = AutoScaleMode.None;
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(S(700), S(420));
        BackColor = Theme.Background;
        ForeColor = Theme.Text;
        Font = Theme.Body(10f);
        Text = "Better Comfy Setup";
        KeyPreview = true;
        DoubleBuffered = true;

        var close = new CloseButton { Bounds = new Rectangle(ClientSize.Width - S(50), S(10), S(40), S(32)) };
        close.Click += (_, _) => Close();
        _title.SetBounds(S(300), S(52), S(370), S(52));
        _title.Font = Theme.Display(24f);
        _title.ForeColor = Theme.Text;
        _subtitle.SetBounds(S(302), S(106), S(360), S(64));
        _subtitle.Font = Theme.Body(10.5f);
        _subtitle.ForeColor = Theme.Muted;
        _page.SetBounds(S(302), S(178), S(360), S(222));
        Controls.AddRange(new Control[] { close, _title, _subtitle, _page });

        _animation.Tick += (_, _) =>
        {
            _frame = (_frame + 1) % Frames;
            Invalidate(new Rectangle(0, 0, S(260), ClientSize.Height));
        };
        _animation.Start();
        ShowWelcome();
    }

    private int S(int value) => (int)Math.Round(value * _scale);

    /// <summary>Shortens text from the middle until it fits, so the start and the end of a path stay readable.</summary>
    private static string CompactPath(string text, Font font, int width)
    {
        if (TextRenderer.MeasureText(text, font).Width <= width) return text;
        int middle = text.Length / 2;
        for (int remove = 4; remove < text.Length - 12; remove += 2)
        {
            string candidate = text.Substring(0, middle - remove / 2) + "…" + text.Substring(middle + (remove + 1) / 2);
            if (TextRenderer.MeasureText(candidate, font).Width <= width) return candidate;
        }
        return text;
    }

    private static Stream Resource(string name) => Assembly.GetExecutingAssembly().GetManifestResourceStream(name);

    // ---------- pages ----------
    private void ShowWelcome()
    {
        _page.Controls.Clear();
        _title.Text = "Better Comfy";
        _subtitle.Text = "Pictures and videos with your own ComfyUI, without wiring nodes. Presets from ultra fast to best.";

        bool installed = Installer.IsInstalled(_options.Folder);
        var desktop = new CheckOption { Text = "Add a desktop shortcut", Checked = _options.DesktopShortcut, Bounds = new Rectangle(0, 0, S(360), S(28)) };
        // One line, shortened in the middle if the path is long, with the full path on hover
        var folder = new Label
        {
            Text = CompactPath($"Installs to {_options.Folder}", _small, S(360)), AutoSize = false, Bounds = new Rectangle(0, S(46), S(360), S(20)),
            Font = _small, ForeColor = Theme.Faint,
        };
        new ToolTip().SetToolTip(folder, _options.Folder);
        var change = Link("Change folder", 0, S(68), ChooseFolder);

        string noteText = installed
            ? "Better Comfy is already installed here and gets updated. Your pictures, videos, settings and gallery stay as they are."
            : "No admin rights needed. Better Comfy works with ComfyUI on this PC (Pinokio, the portable one or ComfyUI Desktop).";
        if (Installer.IsRunning(_options.Folder)) noteText += " Better Comfy is running and will be closed first.";
        if (!Installer.HasPayload) noteText = "This setup file is incomplete. Download it again from the Better Comfy page on GitHub.";
        var note = SmallLabel(noteText, 0, S(112));
        note.ForeColor = Theme.Muted;

        var install = new FlatButton { Primary = true, Text = installed ? "Update" : "Install", Bounds = new Rectangle(S(240), S(178), S(120), S(40)), Enabled = Installer.HasPayload };
        var cancel = new FlatButton { Text = "Cancel", Bounds = new Rectangle(S(130), S(178), S(100), S(40)) };
        _primaryAction = () =>
        {
            if (!install.Enabled) return;
            _options.DesktopShortcut = desktop.Checked;
            StartInstall();
        };
        install.Click += (_, _) => _primaryAction();
        cancel.Click += (_, _) => Close();
        _page.Controls.AddRange(new Control[] { desktop, folder, change, note, install, cancel });
        install.Select();
    }

    private async void StartInstall()
    {
        _installing = true;
        _primaryAction = null;
        _page.Controls.Clear();
        _title.Text = "Installing";
        _subtitle.Text = "This takes a few seconds.";
        var status = new Label { Text = "Getting ready", AutoSize = true, Location = new Point(0, S(20)), Font = Theme.Strong(10.5f), ForeColor = Theme.Text };
        var bar = new ThinProgress { Bounds = new Rectangle(0, S(56), S(360), S(6)) };
        var percent = SmallLabel("0%", 0, S(72));
        _page.Controls.AddRange(new Control[] { status, bar, percent });

        try
        {
            await Task.Run(() => Installer.Install(_options, (value, text) => BeginInvoke((Action)(() =>
            {
                bar.Value = value;
                status.Text = text;
                percent.Text = $"{(int)(value * 100)}%";
            }))));
            _installing = false;
            ShowDone();
        }
        catch (Exception ex)
        {
            _installing = false;
            ShowError(ex.Message);
        }
    }

    private void ShowDone()
    {
        _page.Controls.Clear();
        _title.Text = "All set";
        _subtitle.Text = "Better Comfy finds your ComfyUI by itself. If it can't, it asks for the folder the first time it starts.";

        var open = new FlatButton { Primary = true, Text = "Open Better Comfy", Bounds = new Rectangle(S(200), S(178), S(160), S(40)) };
        var close = new FlatButton { Text = "Close", Bounds = new Rectangle(S(90), S(178), S(100), S(40)) };
        _primaryAction = () =>
        {
            Installer.Launch(_options.Folder);
            Close();
        };
        open.Click += (_, _) => _primaryAction();
        close.Click += (_, _) => Close();
        _page.Controls.AddRange(new Control[] { open, close });
        open.Select();
    }

    private void ShowError(string message)
    {
        _page.Controls.Clear();
        _title.Text = "That didn't work";
        _subtitle.Text = "Setup couldn't finish. Nothing in your pictures, videos or settings was changed.";
        var detail = SmallLabel(message, 0, 0);
        detail.ForeColor = Theme.Muted;
        var close = new FlatButton { Primary = true, Text = "Close", Bounds = new Rectangle(S(240), S(178), S(120), S(40)) };
        close.Click += (_, _) => Close();
        _primaryAction = Close;
        _page.Controls.AddRange(new Control[] { detail, close });
    }

    private void ChooseFolder()
    {
        using var dialog = new FolderBrowserDialog { Description = "Where should Better Comfy be installed?", SelectedPath = Path.GetDirectoryName(_options.Folder) };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        string path = dialog.SelectedPath;
        _options.Folder = path.EndsWith("BetterComfy", StringComparison.OrdinalIgnoreCase) ? path : Path.Combine(path, "BetterComfy");
        ShowWelcome();
    }

    private Label SmallLabel(string text, int x, int y) => new()
    {
        Text = text, AutoSize = true, MaximumSize = new Size(S(360), 0), Location = new Point(x, y), Font = _small, ForeColor = Theme.Faint,
    };

    private LinkLabel Link(string text, int x, int y, Action onClick)
    {
        var link = new LinkLabel
        {
            Text = text, AutoSize = true, Location = new Point(x, y), Font = _small,
            LinkColor = Theme.Text, ActiveLinkColor = Color.White, VisitedLinkColor = Theme.Text, LinkBehavior = LinkBehavior.HoverUnderline,
        };
        link.LinkClicked += (_, _) => onClick();
        return link;
    }

    // ---------- window ----------
    protected override CreateParams CreateParams
    {
        get
        {
            var cp = base.CreateParams;
            cp.ClassStyle |= 0x20000; // CS_DROPSHADOW
            return cp;
        }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        int round = 2; // DWMWCP_ROUND, Windows 11 only
        DwmSetWindowAttribute(Handle, 33, ref round, sizeof(int));
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;

        var art = new Rectangle(0, 0, S(260), ClientSize.Height);
        using (var brush = new SolidBrush(Theme.Panel)) g.FillRectangle(brush, art);
        int size = S(168);
        var dest = new Rectangle((art.Width - size) / 2, (art.Height - size) / 2 - S(12), size, size);
        var source = new Rectangle(_frame % Columns * FrameSize, _frame / Columns * FrameSize, FrameSize, FrameSize);
        g.DrawImage(_sprite, dest, source, GraphicsUnit.Pixel);
        TextRenderer.DrawText(g, $"Version {Installer.Version}", _small, new Point(S(22), ClientSize.Height - S(34)), Theme.Faint);

        using var pen = new Pen(Theme.Line);
        g.DrawLine(pen, art.Right, 0, art.Right, ClientSize.Height);
        g.DrawRectangle(pen, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        if (e.Button != MouseButtons.Left) return;
        ReleaseCapture();
        SendMessage(Handle, 0xA1 /* WM_NCLBUTTONDOWN */, (IntPtr)2 /* HTCAPTION */, IntPtr.Zero);
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        base.OnKeyDown(e);
        if (e.KeyCode == Keys.Escape) Close();
        else if (e.KeyCode == Keys.Enter && _primaryAction != null && !(ActiveControl is CheckOption)) _primaryAction();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (_installing) e.Cancel = true; // half copied files would help nobody
        base.OnFormClosing(e);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _animation.Dispose();
            _sprite.Dispose();
            _small.Dispose();
        }
        base.Dispose(disposing);
    }

    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
    [DllImport("user32.dll")] private static extern bool ReleaseCapture();
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);
}
