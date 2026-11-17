// Оболочка для игр Мартышки: ОДИН .exe — окно с встроенным браузером (WebView2 = движок Edge из Windows 11),
// внутри спрятаны сама игра (game.html) и библиотеки WebView2. Рядом с exe ничего класть не нужно.
using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace MartyshkaGame
{
    static class Program
    {
        public const string Title = "__TITLE__";
        public const string Id = "__ID__";
        public static string Home;          // %LOCALAPPDATA%\MartyshkaGames\<id>

        [STAThread]
        static void Main()
        {
            Home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MartyshkaGames", Id);
            Directory.CreateDirectory(Home);
            // библиотеки WebView2 лежат внутри exe — отдаём их .NET, когда он их попросит
            AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
            {
                string name = new AssemblyName(e.Name).Name + ".dll";
                byte[] data = Res(name);
                return data == null ? null : Assembly.Load(data);
            };
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Run();
        }

        public static byte[] Res(string name)
        {
            using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
            {
                if (s == null) return null;
                var ms = new MemoryStream(); s.CopyTo(ms); return ms.ToArray();
            }
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        static void Run() { Application.Run(new GameForm()); }
    }

    class GameForm : Form, IMessageFilter
    {
        // Если клавиши пришли в саму рамку окна, а не в браузер (бывает сразу после запуска), — передаём их в игру.
        // Когда фокус уже в браузере, эти сообщения сюда не приходят, так что двойных нажатий не будет.
        public bool PreFilterMessage(ref Message m)
        {
            bool down = m.Msg == 0x100 || m.Msg == 0x104, up = m.Msg == 0x101 || m.Msg == 0x105;
            if (!(down || up) || web == null || web.CoreWebView2 == null) return false;
            Keys k = (Keys)((int)m.WParam & 0xFFFF);
            if (down && (k == Keys.F11 || (k == Keys.Escape && full))) { ToggleFull(); return true; }
            string code = Code(k);
            if (code == null) return false;
            bool repeat = down && (((long)m.LParam >> 30) & 1) == 1;
            string key = code.StartsWith("Key") ? code.Substring(3).ToLower() : code.StartsWith("Digit") ? code.Substring(5)
                       : code == "Space" ? " " : code.StartsWith("Shift") ? "Shift" : code.StartsWith("Control") ? "Control" : code;
            web.CoreWebView2.ExecuteScriptAsync("window.dispatchEvent(new KeyboardEvent('" + (down ? "keydown" : "keyup") +
                "',{code:'" + code + "',key:'" + key + "',repeat:" + (repeat ? "true" : "false") + ",bubbles:true,cancelable:true}));");
            if (down) GiveFocus();
            return true;
        }

        static string Code(Keys k)
        {
            if (k >= Keys.A && k <= Keys.Z) return "Key" + k;
            if (k >= Keys.D0 && k <= Keys.D9) return "Digit" + (char)('0' + (k - Keys.D0));
            switch (k)
            {
                case Keys.Left: return "ArrowLeft"; case Keys.Right: return "ArrowRight";
                case Keys.Up: return "ArrowUp"; case Keys.Down: return "ArrowDown";
                case Keys.Space: return "Space"; case Keys.Enter: return "Enter"; case Keys.Escape: return "Escape";
                case Keys.ShiftKey: return "ShiftLeft"; case Keys.ControlKey: return "ControlLeft"; case Keys.Tab: return "Tab";
            }
            return null;
        }

        Microsoft.Web.WebView2.WinForms.WebView2 web;
        FormWindowState prevState; FormBorderStyle prevBorder; bool full;

        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int val, int size);

        public GameForm()
        {
            Text = Program.Title;
            BackColor = Color.FromArgb(11, 13, 18);
            StartPosition = FormStartPosition.CenterScreen;
            Size = new Size(1280, 800);
            MinimumSize = new Size(640, 420);
            KeyPreview = true;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            web = new Microsoft.Web.WebView2.WinForms.WebView2();
            web.Dock = DockStyle.Fill;
            web.DefaultBackgroundColor = Color.FromArgb(11, 13, 18);
            Controls.Add(web);
            Load += async (s, e) => await Init();
            Application.AddMessageFilter(this);
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try { int on = 1; DwmSetWindowAttribute(Handle, 20, ref on, 4); } catch { }   // тёмная шапка окна
        }

        async System.Threading.Tasks.Task Init()
        {
            try
            {
                // родной загрузчик WebView2 достаём из exe во временную папку игры
                string native = Path.Combine(Program.Home, "runtime");
                Directory.CreateDirectory(native);
                string loader = Path.Combine(native, "WebView2Loader.dll");
                if (!File.Exists(loader)) File.WriteAllBytes(loader, Program.Res("WebView2Loader.dll"));
                Microsoft.Web.WebView2.Core.CoreWebView2Environment.SetLoaderDllFolderPath(native);
                // сама игра
                string site = Path.Combine(Program.Home, "site");
                Directory.CreateDirectory(site);
                File.WriteAllBytes(Path.Combine(site, "index.html"), Program.Res("game.html"));
                // только для проверки Мартышкой: MARTYSHKA_GAME_DEBUG=порт открывает отладочный вход
                string dbg = Environment.GetEnvironmentVariable("MARTYSHKA_GAME_DEBUG");
                var opts = string.IsNullOrEmpty(dbg) ? null :
                    new Microsoft.Web.WebView2.Core.CoreWebView2EnvironmentOptions("--remote-debugging-port=" + dbg);
                var env = await Microsoft.Web.WebView2.Core.CoreWebView2Environment.CreateAsync(null,
                    Path.Combine(Program.Home, "webview"), opts);
                await web.EnsureCoreWebView2Async(env);
                var cw = web.CoreWebView2;
                cw.Settings.AreDevToolsEnabled = false;
                cw.Settings.IsStatusBarEnabled = false;
                cw.Settings.AreDefaultContextMenusEnabled = false;
                cw.Settings.IsZoomControlEnabled = false;
                // адрес https://game.local — чтобы у игры работали рекорды (localStorage)
                cw.SetVirtualHostNameToFolderMapping("game.local", site,
                    Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind.Allow);
                // как только игра загрузилась — клавиатура сразу в игре (иначе пробел «уходит» в рамку окна)
                cw.NavigationCompleted += (s, e) => GiveFocus();
                Activated += (s, e) => GiveFocus();
                cw.Navigate("https://game.local/index.html");
            }
            catch (Exception ex)
            {
                MessageBox.Show("Не удалось запустить игру.\n\nНужен Microsoft Edge WebView2 Runtime (в Windows 11 он есть, " +
                    "в Windows 10 его можно скачать с сайта Microsoft).\n\n" + ex.Message, Program.Title,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
            }
        }

        // клавиатура — внутрь игры: просим сам браузер забрать фокус (просто Focus() у окна-обёртки не хватает)
        void GiveFocus()
        {
            BeginInvoke((Action)(() =>
            {
                try
                {
                    if (web.CoreWebView2 == null) return;
                    web.Focus();
                    var ctl = typeof(Microsoft.Web.WebView2.WinForms.WebView2).GetField("_coreWebView2Controller",
                        BindingFlags.NonPublic | BindingFlags.Instance);
                    var c = ctl == null ? null : ctl.GetValue(web) as Microsoft.Web.WebView2.Core.CoreWebView2Controller;
                    if (c != null) c.MoveFocus(Microsoft.Web.WebView2.Core.CoreWebView2MoveFocusReason.Programmatic);
                    web.CoreWebView2.ExecuteScriptAsync("window.focus();");
                }
                catch { }
            }));
        }

        // F11 — во весь экран, Esc — выйти из полного экрана
        protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
        {
            if (keyData == Keys.F11 || (keyData == Keys.Escape && full)) { ToggleFull(); return true; }
            return base.ProcessCmdKey(ref msg, keyData);
        }

        void ToggleFull()
        {
            if (!full) { prevState = WindowState; prevBorder = FormBorderStyle; FormBorderStyle = FormBorderStyle.None;
                         WindowState = FormWindowState.Normal; WindowState = FormWindowState.Maximized; full = true; }
            else { FormBorderStyle = prevBorder; WindowState = prevState; full = false; }
        }
    }
}
