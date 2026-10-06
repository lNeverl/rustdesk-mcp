#[cfg(windows)]
fn main() {
    if let Err(err) = librustdesk::mcp_bridge::run_cli() {
        eprintln!("MCP bridge stopped: {err}");
        std::process::exit(1);
    }
}

#[cfg(not(windows))]
fn main() {
    eprintln!("MCP bridge currently supports Windows x64 only");
    std::process::exit(1);
}
