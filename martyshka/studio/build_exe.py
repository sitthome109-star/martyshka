"""Собрать «Мартышка Студия.exe» в папке проекта (компилятор C# из .NET Framework, он есть в каждой Windows)."""
import subprocess
from pathlib import Path

from .. import config

CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")


def build() -> Path:
    src = Path(__file__).parent / "launcher.cs"
    out = config.PROJECT_DIR / "Мартышка Студия.exe"
    icon = config.PROJECT_DIR / "martyshka" / "martyshka.ico"
    args = [str(CSC), "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+", f"/out:{out}",
            "/r:System.Windows.Forms.dll"]
    if icon.exists():
        args.append(f"/win32icon:{icon}")
    args.append(str(src))
    r = subprocess.run(args, capture_output=True, text=True, encoding="cp866", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)
    return out


if __name__ == "__main__":
    print(build())
