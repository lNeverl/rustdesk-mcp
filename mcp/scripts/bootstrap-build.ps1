param([string]$Workspace = (Resolve-Path "$PSScriptRoot/../..").Path, [switch]$NativeOnly)
$ErrorActionPreference = 'Stop'
$toolsRoot = Join-Path $Workspace 'build/mcp-tools'
New-Item -ItemType Directory -Force -Path $toolsRoot | Out-Null
$env:CARGO_HOME = Join-Path $Workspace 'build/c'
$env:RUSTUP_HOME = Join-Path $toolsRoot 'rustup'
$env:VCPKG_DOWNLOADS = Join-Path $toolsRoot 'vcpkg-downloads'
$env:VCPKG_DEFAULT_BINARY_CACHE = Join-Path $toolsRoot 'vcpkg-cache'
$env:VCPKG_BINARY_SOURCES = 'clear'
$env:VCPKG_DISABLE_METRICS = '1'
$env:TEMP = Join-Path $toolsRoot 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP,$env:VCPKG_DEFAULT_BINARY_CACHE | Out-Null
$env:PATH = "$(Join-Path $toolsRoot 'python/cmake/data/bin');$env:PATH"
$env:UV_CACHE_DIR = Join-Path $toolsRoot 'uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
if (!(Test-Path (Join-Path $toolsRoot 'python/clang/native/libclang.dll')) -or
    !(Test-Path (Join-Path $toolsRoot 'python/cmake/data/bin/cmake.exe'))) {
    & uv pip install --python (Get-Command python -ErrorAction Stop).Source --target (Join-Path $toolsRoot 'python') libclang==18.1.1 cmake==4.4.4
    if ($LASTEXITCODE) { throw 'Project-local native helper installation failed' }
}
$rustupInstaller = Join-Path $toolsRoot 'rustup-init.exe'
if (!$NativeOnly -and (!(Test-Path (Join-Path $env:RUSTUP_HOME 'toolchains/stable-x86_64-pc-windows-msvc/bin/rustc.exe')) -or
    !(Test-Path (Join-Path $env:CARGO_HOME 'bin/cargo.exe')) -or !(Test-Path (Join-Path $env:CARGO_HOME 'bin/rustc.exe')))) {
    Invoke-WebRequest 'https://win.rustup.rs/x86_64' -OutFile $rustupInstaller
    & $rustupInstaller -y --no-modify-path --profile minimal --default-toolchain stable
    if ($LASTEXITCODE) { throw 'Rust toolchain installation failed' }
}
$vcpkgRoot = Join-Path $toolsRoot 'vcpkg'
if (!(Test-Path (Join-Path $vcpkgRoot '.git'))) {
    git init --quiet $vcpkgRoot
    if ($LASTEXITCODE) { throw 'vcpkg repository initialization failed' }
    git -C $vcpkgRoot remote add origin https://github.com/microsoft/vcpkg.git
    if ($LASTEXITCODE) { throw 'vcpkg remote setup failed' }
}
git -C $vcpkgRoot rev-parse --verify --quiet '9e593bb18ea69cc5095e012465dcd675a822ed0d^{commit}' | Out-Null
if ($LASTEXITCODE) {
    git -C $vcpkgRoot fetch --quiet --depth 1 origin 9e593bb18ea69cc5095e012465dcd675a822ed0d
    if ($LASTEXITCODE) { throw 'vcpkg baseline fetch failed' }
}
git -C $vcpkgRoot checkout --quiet 9e593bb18ea69cc5095e012465dcd675a822ed0d
if ($LASTEXITCODE) { throw 'vcpkg baseline checkout failed' }
if (!(Test-Path (Join-Path $vcpkgRoot 'vcpkg.exe'))) {
    & (Join-Path $vcpkgRoot 'bootstrap-vcpkg.bat') -disableMetrics
    if ($LASTEXITCODE) { throw 'vcpkg bootstrap failed' }
}
$env:VCPKG_ROOT = $vcpkgRoot
& (Join-Path $vcpkgRoot 'vcpkg.exe') install --classic --triplet x64-windows-static --overlay-ports="$Workspace/res/vcpkg" libvpx libyuv aom opus libjpeg-turbo
if ($LASTEXITCODE) { throw 'vcpkg dependency build failed' }
Write-Host 'Build tools and native dependencies ready.'
