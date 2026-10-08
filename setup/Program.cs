using System;
using System.IO;
using System.Windows.Forms;

namespace BetterComfySetup;

internal sealed class SetupOptions
{
    public string Folder = Installer.DefaultFolder;
    public bool DesktopShortcut = true;
    public bool StartMenuShortcut = true;
    public bool Register = true;
    public bool Launch = true;
    public bool Silent;

    /// <summary>
    /// /silent installs without a window, /dir="C:\path" picks the folder, /noshortcuts and /noregister leave the Start
    /// menu, desktop and "Installed apps" alone (handy for testing).
    /// </summary>
    public static SetupOptions Parse(string[] args)
    {
        var options = new SetupOptions();
        foreach (string raw in args)
        {
            string arg = raw.TrimStart('/', '-');
            if (arg.StartsWith("dir=", StringComparison.OrdinalIgnoreCase)) options.Folder = Path.GetFullPath(arg.Substring(4).Trim('"'));
            else if (arg.Equals("silent", StringComparison.OrdinalIgnoreCase)) { options.Silent = true; options.Launch = false; }
            else if (arg.Equals("noshortcuts", StringComparison.OrdinalIgnoreCase)) options.DesktopShortcut = options.StartMenuShortcut = false;
            else if (arg.Equals("noregister", StringComparison.OrdinalIgnoreCase)) options.Register = false;
        }
        return options;
    }
}

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        var options = SetupOptions.Parse(args);
        if (options.Silent)
        {
            try
            {
                Installer.Install(options, null);
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.Message);
                return 1;
            }
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SetupForm(options));
        return 0;
    }
}
