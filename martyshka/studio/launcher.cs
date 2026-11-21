// Запускалка «Мартышка.exe»: тихо запускает studio.py через Python проекта (без чёрного окна) и передаёт ключи (--autostart).
// Собирается командой: .venv\Scripts\python.exe -m martyshka.studio.build_exe
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static void Main(string[] argv)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, ".venv", "Scripts", "pythonw.exe");
        string script = Path.Combine(dir, "studio.py");
        if (!File.Exists(py) || !File.Exists(script))
        {
            MessageBox.Show("Не нашла Python или studio.py рядом с этим файлом.\nПапка: " + dir,
                            "Мартышка", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        string extra = argv.Length > 0 ? " " + string.Join(" ", argv) : "";
        var psi = new ProcessStartInfo(py, "\"" + script + "\"" + extra);
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
    }
}
