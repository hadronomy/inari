#[cfg(target_os = "windows")]
fn main() {
    use std::path::Path;

    let icon = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../target/release/windows/assets/InariDeviceCenter.ico");
    println!("cargo:rerun-if-changed={}", icon.display());

    if icon.exists() {
        winresource::WindowsResource::new()
            .set_icon(
                icon.to_str()
                    .expect("the workspace path is UTF-8"),
            )
            .compile()
            .expect("failed to embed the Device Center icon");
    } else {
        println!(
            "cargo:warning=Device Center icon not found at {}; the executable will use its packaged MSIX identity",
            icon.display()
        );
    }
}

#[cfg(not(target_os = "windows"))]
fn main() {}
