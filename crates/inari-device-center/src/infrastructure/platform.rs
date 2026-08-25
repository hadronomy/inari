use gpui::{App, Window};

pub fn forward_activation(invitation: Option<&str>) -> bool {
    #[cfg(windows)]
    {
        use std::{
            fs::OpenOptions,
            io::Write as _,
            thread,
            time::{Duration, Instant},
        };

        const PIPE: &str = r"\\.\pipe\Inari.DeviceCenter.Activation";
        let started = Instant::now();
        loop {
            match OpenOptions::new()
                .write(true)
                .open(PIPE)
            {
                Ok(mut pipe) => {
                    let message_type = u8::from(invitation.is_some());
                    if pipe.write_all(&[message_type]).is_err() {
                        return false;
                    }
                    if let Some(invitation) = invitation
                        && pipe
                            .write_all(invitation.as_bytes())
                            .is_err()
                    {
                        return false;
                    }
                    return pipe.flush().is_ok();
                },
                Err(_) if started.elapsed() < Duration::from_millis(180) => {
                    thread::sleep(Duration::from_millis(30));
                },
                Err(_) => return false,
            }
        }
    }

    #[cfg(not(windows))]
    {
        let _ = invitation;
        false
    }
}

pub fn hide_window(window: &mut Window, cx: &mut App) {
    hide_window_on_platform(window, cx);
}

#[cfg(windows)]
fn hide_window_on_platform(window: &mut Window, _: &mut App) {
    if let Some(handle) = windows_handle(window) {
        unsafe {
            let _ = windows::Win32::UI::WindowsAndMessaging::ShowWindow(
                handle,
                windows::Win32::UI::WindowsAndMessaging::SW_HIDE,
            );
        }
        return;
    }
    window.minimize_window();
}

#[cfg(target_os = "macos")]
fn hide_window_on_platform(_: &mut Window, cx: &mut App) {
    cx.hide();
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn hide_window_on_platform(window: &mut Window, _: &mut App) {
    window.minimize_window();
}

pub fn show_window(window: &mut Window, cx: &mut App) {
    #[cfg(windows)]
    if let Some(handle) = windows_handle(window) {
        unsafe {
            let _ = windows::Win32::UI::WindowsAndMessaging::ShowWindow(
                handle,
                windows::Win32::UI::WindowsAndMessaging::SW_RESTORE,
            );
        }
    }

    cx.activate(true);
    window.activate_window();
}

pub fn start_window_drag(window: &Window) {
    start_window_drag_on_platform(window);
}

#[cfg(windows)]
fn start_window_drag_on_platform(window: &Window) {
    use windows::Win32::{
        Foundation::WPARAM,
        UI::{
            Input::KeyboardAndMouse::ReleaseCapture,
            WindowsAndMessaging::{HTCAPTION, SendMessageW, WM_NCLBUTTONDOWN},
        },
    };

    let Some(handle) = windows_handle(window) else {
        return;
    };

    unsafe {
        let _ = ReleaseCapture();
        let _ = SendMessageW(handle, WM_NCLBUTTONDOWN, Some(WPARAM(HTCAPTION as usize)), None);
    }
}

// Only Windows needs the nudge. macOS drags through the native title bar,
// and Linux drags through TitleBar's deferred `start_window_move`.
#[cfg(not(windows))]
fn start_window_drag_on_platform(_: &Window) {}

#[cfg(windows)]
fn windows_handle(window: &Window) -> Option<windows::Win32::Foundation::HWND> {
    use raw_window_handle::RawWindowHandle;

    let handle = raw_window_handle::HasWindowHandle::window_handle(window).ok()?;
    let RawWindowHandle::Win32(handle) = handle.as_raw() else {
        return None;
    };
    Some(windows::Win32::Foundation::HWND(handle.hwnd.get() as *mut std::ffi::c_void))
}
