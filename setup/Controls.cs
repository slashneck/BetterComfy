using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Linq;
using System.Windows.Forms;

namespace BetterComfySetup;

internal static class Theme
{
    public static readonly Color Background = Color.FromArgb(10, 10, 10);
    public static readonly Color Panel = Color.FromArgb(17, 17, 17);
    public static readonly Color Line = Color.FromArgb(42, 42, 42);
    public static readonly Color Text = Color.FromArgb(244, 244, 244);
    public static readonly Color Muted = Color.FromArgb(158, 158, 158);
    public static readonly Color Faint = Color.FromArgb(105, 105, 105);
    public static readonly Color Accent = Color.FromArgb(139, 124, 246);
    public static readonly Color AccentHover = Color.FromArgb(160, 148, 248);
    public static readonly Color AccentPressed = Color.FromArgb(118, 105, 210);

    private static readonly bool HasVariable = FontFamily.Families.Any(f => f.Name == "Segoe UI Variable Display");

    public static Font Body(float size) => new(HasVariable ? "Segoe UI Variable Text" : "Segoe UI", size);
    public static Font Strong(float size) => new(HasVariable ? "Segoe UI Variable Text Semibold" : "Segoe UI Semibold", size);
    public static Font Display(float size) => new(HasVariable ? "Segoe UI Variable Display Semibold" : "Segoe UI Semibold", size);

    public static GraphicsPath Rounded(RectangleF r, float radius)
    {
        float d = Math.Min(radius * 2, Math.Min(r.Width, r.Height));
        var path = new GraphicsPath();
        path.AddArc(r.X, r.Y, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }
}

/// <summary>Base for the owner drawn controls: double buffered, hover and press tracking, keyboard click.</summary>
internal abstract class DrawnControl : Control
{
    protected bool Hover, Pressed;

    protected DrawnControl()
    {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                 ControlStyles.ResizeRedraw | ControlStyles.SupportsTransparentBackColor | ControlStyles.Selectable, true);
        BackColor = Color.Transparent;
        ForeColor = Theme.Text;
        Cursor = Cursors.Hand;
        TabStop = true;
    }

    protected float Dpi => DeviceDpi / 96f;

    protected override void OnMouseEnter(EventArgs e) { Hover = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { Hover = Pressed = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { Pressed = true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { Pressed = false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }
    protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
    protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (e.KeyCode is Keys.Space or Keys.Enter)
        {
            OnClick(EventArgs.Empty);
            e.Handled = true;
        }
        base.OnKeyDown(e);
    }

    protected static void Smooth(Graphics g)
    {
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
    }
}

internal sealed class FlatButton : DrawnControl
{
    public bool Primary { get; set; }

    public FlatButton()
    {
        Font = Theme.Strong(10f);
        Size = new Size(120, 38);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        Smooth(g);
        var rect = new RectangleF(0.5f, 0.5f, Width - 1, Height - 1);
        using var path = Theme.Rounded(rect, 9 * Dpi);
        Color fill, border, text;
        if (Primary)
        {
            fill = Pressed ? Theme.AccentPressed : Hover ? Theme.AccentHover : Theme.Accent;
            border = fill;
            text = Color.White;
        }
        else
        {
            fill = Pressed ? Color.FromArgb(38, 38, 38) : Hover ? Color.FromArgb(28, 28, 28) : Theme.Background;
            border = Hover ? Color.FromArgb(70, 70, 70) : Theme.Line;
            text = Theme.Text;
        }
        if (!Enabled)
        {
            fill = Color.FromArgb(90, fill);
            border = Color.FromArgb(90, border);
            text = Color.FromArgb(110, text);
        }
        using (var brush = new SolidBrush(fill)) g.FillPath(brush, path);
        using (var pen = new Pen(border)) g.DrawPath(pen, path);
        if (Focused && ShowFocusCues)
        {
            using var focus = Theme.Rounded(RectangleF.Inflate(rect, -3 * Dpi, -3 * Dpi), 7 * Dpi);
            using var pen = new Pen(Primary ? Theme.Background : Theme.Muted) { DashStyle = DashStyle.Dot };
            g.DrawPath(pen, focus);
        }
        TextRenderer.DrawText(g, Text, Font, ClientRectangle, text, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
    }
}

internal sealed class CheckOption : DrawnControl
{
    private bool _checked;

    public bool Checked
    {
        get => _checked;
        set { _checked = value; Invalidate(); }
    }

    public CheckOption()
    {
        Font = Theme.Body(10f);
        Size = new Size(320, 28);
    }

    protected override void OnClick(EventArgs e)
    {
        Checked = !Checked;
        base.OnClick(e);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        Smooth(g);
        float box = 18 * Dpi;
        var rect = new RectangleF(0.5f, (Height - box) / 2, box, box);
        using var path = Theme.Rounded(rect, 5 * Dpi);
        if (Checked)
        {
            using (var brush = new SolidBrush(Hover ? Theme.AccentHover : Theme.Accent)) g.FillPath(brush, path);
            using var pen = new Pen(Color.White, 2.2f * Dpi) { StartCap = LineCap.Round, EndCap = LineCap.Round, LineJoin = LineJoin.Round };
            g.DrawLines(pen, new[]
            {
                new PointF(rect.X + box * 0.26f, rect.Y + box * 0.52f),
                new PointF(rect.X + box * 0.44f, rect.Y + box * 0.7f),
                new PointF(rect.X + box * 0.76f, rect.Y + box * 0.32f),
            });
        }
        else
        {
            using var pen = new Pen(Hover ? Theme.Muted : Color.FromArgb(85, 85, 85), 1.4f * Dpi);
            g.DrawPath(pen, path);
        }
        var textRect = new Rectangle((int)(box + 10 * Dpi), 0, Width - (int)(box + 10 * Dpi), Height);
        TextRenderer.DrawText(g, Text, Font, textRect, Theme.Text, TextFormatFlags.VerticalCenter | TextFormatFlags.Left | TextFormatFlags.EndEllipsis);
        if (Focused && ShowFocusCues)
        {
            using var pen = new Pen(Theme.Muted) { DashStyle = DashStyle.Dot };
            g.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
        }
    }
}

internal sealed class CloseButton : DrawnControl
{
    public CloseButton()
    {
        Size = new Size(40, 32);
        TabStop = false;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        Smooth(g);
        if (Hover)
        {
            using var path = Theme.Rounded(new RectangleF(0.5f, 0.5f, Width - 1, Height - 1), 7 * Dpi);
            using var brush = new SolidBrush(Color.FromArgb(34, 34, 34));
            g.FillPath(brush, path);
        }
        float s = 5 * Dpi, cx = Width / 2f, cy = Height / 2f;
        using var pen = new Pen(Hover ? Theme.Text : Theme.Muted, 1.4f * Dpi) { StartCap = LineCap.Round, EndCap = LineCap.Round };
        g.DrawLine(pen, cx - s, cy - s, cx + s, cy + s);
        g.DrawLine(pen, cx + s, cy - s, cx - s, cy + s);
    }
}

internal sealed class ThinProgress : Control
{
    private double _value;

    public double Value
    {
        get => _value;
        set { _value = Math.Max(0, Math.Min(1, value)); Invalidate(); }
    }

    public ThinProgress()
    {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        Size = new Size(320, 6);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Parent?.BackColor ?? Theme.Background);
        var track = new RectangleF(0, 0, Width, Height);
        using (var path = Theme.Rounded(track, Height / 2f))
        using (var brush = new SolidBrush(Color.FromArgb(36, 36, 36)))
            g.FillPath(brush, path);
        if (_value <= 0) return;
        var fill = new RectangleF(0, 0, Math.Max(Height, (float)(Width * _value)), Height);
        using (var path = Theme.Rounded(fill, Height / 2f))
        using (var brush = new SolidBrush(Theme.Accent))
            g.FillPath(brush, path);
    }
}
