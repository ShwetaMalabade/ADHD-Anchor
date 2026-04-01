"""
Anchor Overlay -- Native macOS floating nudge window
Spawned as a subprocess from server.py. Shows a translucent overlay
on top of ALL apps (including with DND on) then auto-dismisses.

Usage: python overlay.py "Your nudge message here" [--options "Opt1||Opt2||Opt3"] [--duration 8]
Prints the selected option to stdout so the server can read it.
"""

import sys
import signal
import subprocess
import warnings
import AppKit
import objc
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper

warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)

ANCHOR_URL = "http://localhost:8080"


def bring_anchor_to_front():
    """Bring the existing Chrome tab with Anchor to the front (no new tab)"""
    try:
        script = '''
        tell application "Google Chrome"
            activate
            set found to false
            repeat with w in windows
                set tabIndex to 0
                repeat with t in tabs of w
                    set tabIndex to tabIndex + 1
                    if URL of t contains "localhost:8080" then
                        set active tab index of w to tabIndex
                        set index of w to 1
                        set found to true
                        exit repeat
                    end if
                end repeat
                if found then exit repeat
            end repeat
        end tell
        '''
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except:
        pass


class OverlayDelegate(NSObject):
    def init(self):
        self = objc.super(OverlayDelegate, self).init()
        if self is None:
            return None
        self.selected_option = None
        self.window = None
        self.buttons = []
        return self

    def createOverlayWithMessage_options_duration_(self, message, options, duration):
        screen = AppKit.NSScreen.mainScreen()
        screen_frame = screen.frame()
        sw = screen_frame.size.width

        # Overlay size
        width = 360
        base_height = 110  # Extra space for close button
        has_options = options and len(options) > 0
        button_height = 36 * len(options) + 8 * len(options) if has_options else 0
        height = base_height + button_height + (20 if has_options else 0)

        # Position: bottom-right corner
        x = sw - width - 24
        y = 80

        # Create borderless, transparent, always-on-top window
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, y), (width, height)),
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setLevel_(AppKit.NSStatusWindowLevel + 1)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.window.setHasShadow_(True)
        self.window.setIgnoresMouseEvents_(False)
        self.window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary |
            AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        # Content view
        content = AppKit.NSView.alloc().initWithFrame_(((0, 0), (width, height)))

        # Background: dark rounded rect
        bg = AppKit.NSView.alloc().initWithFrame_(((0, 0), (width, height)))
        content.addSubview_(bg)
        bg.setWantsLayer_(True)
        bg.layer().setCornerRadius_(16)
        bg.layer().setMasksToBounds_(True)
        bg.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.12, 0.14, 0.95).CGColor()
        )
        bg.layer().setBorderColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.3, 0.32, 0.6).CGColor()
        )
        bg.layer().setBorderWidth_(0.5)

        # Close (X) button -- top right, larger click target
        close_btn = AppKit.NSButton.alloc().initWithFrame_(((width - 44, height - 38), (32, 32)))
        close_btn.setTitle_("  \u2715  ")
        close_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        close_btn.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightMedium))
        close_btn.setWantsLayer_(True)
        close_btn.layer().setCornerRadius_(8)
        close_btn.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.25, 0.25, 0.27, 0.8).CGColor()
        )
        close_btn.setContentTintColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.8, 0.8, 0.8, 1.0)
        )
        close_btn.setTarget_(self)
        close_btn.setAction_(objc.selector(self.closeClicked_, signature=b"v@:@"))
        content.addSubview_(close_btn)

        # Title label "Anchor"
        title_label = AppKit.NSTextField.labelWithString_("Anchor")
        title_label.setFrame_(((20, height - 32), (width - 70, 18)))
        title_label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightSemibold))
        title_label.setTextColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.75, 0.55, 1.0))
        title_label.setBezeled_(False)
        title_label.setDrawsBackground_(False)
        title_label.setEditable_(False)
        title_label.setSelectable_(False)
        content.addSubview_(title_label)

        # Message label
        msg_label = AppKit.NSTextField.wrappingLabelWithString_(message)
        msg_y = height - 84 if not has_options else height - 80
        msg_label.setFrame_(((20, msg_y), (width - 40, 48)))
        msg_label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13.5, AppKit.NSFontWeightMedium))
        msg_label.setTextColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.92, 0.92, 0.92, 1.0))
        msg_label.setBezeled_(False)
        msg_label.setDrawsBackground_(False)
        msg_label.setEditable_(False)
        msg_label.setSelectable_(False)
        content.addSubview_(msg_label)

        # Option buttons
        if has_options:
            btn_y = 16
            for i, opt in enumerate(options):
                btn = AppKit.NSButton.alloc().initWithFrame_(((20, btn_y), (width - 40, 36)))
                btn.setTitle_(opt)
                btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
                btn.setFont_(AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium))
                btn.setWantsLayer_(True)

                if i == 0:
                    btn.layer().setBackgroundColor_(
                        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.45, 0.68, 0.45, 0.9).CGColor()
                    )
                    btn.layer().setCornerRadius_(10)
                    btn.setContentTintColor_(AppKit.NSColor.whiteColor())
                else:
                    btn.layer().setBackgroundColor_(
                        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.25, 0.25, 0.27, 0.8).CGColor()
                    )
                    btn.layer().setCornerRadius_(10)
                    btn.setContentTintColor_(
                        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.85, 0.85, 0.85, 1.0)
                    )

                btn.setTag_(i)
                btn.setTarget_(self)
                btn.setAction_(objc.selector(self.buttonClicked_, signature=b"v@:@"))
                content.addSubview_(btn)
                self.buttons.append(btn)
                btn_y += 44

        self.window.setContentView_(content)

        # Fade in
        self.window.setAlphaValue_(0.0)
        self.window.makeKeyAndOrderFront_(None)
        def fade_in(ctx):
            ctx.setDuration_(0.25)
            self.window.animator().setAlphaValue_(1.0)
        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(fade_in, None)

        # Auto-dismiss: after duration for simple messages, after 60s for options (prevents stale overlays)
        timeout = duration if not has_options else 60
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            timeout, self, objc.selector(self.autoDismiss_, signature=b"v@:@"), None, False
        )

    @objc.python_method
    def dismiss(self):
        if self.window:
            def fade_out(ctx):
                ctx.setDuration_(0.2)
                self.window.animator().setAlphaValue_(0.0)
            def on_done():
                AppHelper.stopEventLoop()
            AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(fade_out, on_done)

    def closeClicked_(self, sender):
        """X button -- dismiss without any action"""
        print("__dismissed__", flush=True)
        self.dismiss()

    def buttonClicked_(self, sender):
        opt_title = sender.title()
        self.selected_option = opt_title
        print(opt_title, flush=True)
        # Bring Anchor frontend to front so user lands back on the focus screen
        bring_anchor_to_front()
        self.dismiss()

    def autoDismiss_(self, timer):
        self.dismiss()


def main():
    message = sys.argv[1] if len(sys.argv) > 1 else "Hey, stay focused!"
    options = []
    duration = 8

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--options" and i + 1 < len(sys.argv):
            options = [o.strip() for o in sys.argv[i + 1].split("||") if o.strip()]
            i += 2
        elif sys.argv[i] == "--duration" and i + 1 < len(sys.argv):
            duration = float(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    delegate = OverlayDelegate.alloc().init()
    delegate.createOverlayWithMessage_options_duration_(message, options, duration)

    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
