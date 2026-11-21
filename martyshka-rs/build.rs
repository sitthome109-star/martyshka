fn main() {
    #[cfg(windows)]
    {
        let ico = std::path::Path::new("ui/studio.ico");
        if ico.exists() {
            let mut res = winresource::WindowsResource::new();
            res.set_icon(ico.to_str().unwrap());
            let _ = res.compile();
        }
    }
}
