// Запускалка «Мартышка Студия.exe»: тихо запускает studio.py через Python проекта (без чёрного окна).
// Собирается командой: .venv\Scripts\python.exe -m martyshka.studio.build_exe
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static void Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, ".venv", "Scripts", "pythonw.exe");
        string script = Path.Combine(dir, "studio.py");
        if (!File.Exists(py) || !File.Exists(script))
        {
            MessageBox.Show("Не нашла Python или studio.py рядом с этим файлом.\nПапка: " + dir,
                            "Мартышка Студия", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        var psi = new ProcessStartInfo(py, "\"" + script + "\"");
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
    }
}
