#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════════╗
║             Z A D E N X X   A C C O U N T   M A N A G E R           ║
║                                                                       ║
║  Runtime  : Python 3.10+ · Flet 0.21+                               ║
║  Desktop  : Windows 11 / macOS / Linux  (python -m flet run app.py) ║
║  Mobile   : Android / iOS               (flet build apk / ipa)      ║
║                                                                       ║
║  Architecture                                                         ║
║  ─────────────────────────────────────────────────────────────────── ║
║  main(page)                                                           ║
║    │── Storage layer  (pathlib, JSON, platform-agnostic)             ║
║    │── Transparency slider  (always-visible top bar)                 ║
║    │── body_slot  ──► AUTH_VIEW  or  DASHBOARD_VIEW                 ║
║    │── Add-account dialog  (ft.AlertDialog in page.overlay)          ║
║    └── Root container  (opacity target)                              ║
╚═══════════════════════════════════════════════════════════════════════╝
"""

# ── Standard library ───────────────────────────────────────────────────────────
import json
import os
import platform
import uuid
from datetime import datetime
from pathlib import Path

# ── Flet ───────────────────────────────────────────────────────────────────────
import flet as ft


# ══════════════════════════════════════════════════════════════════════════════
# §1  CONSTANTS & PALETTE
# ══════════════════════════════════════════════════════════════════════════════

# Hardcoded owner credentials — verified inside attempt_login()
OWNER_USERNAME: str = "x"
OWNER_PASSWORD: str = "xisthebest123"

# Application identity strings
APP_TITLE    = "ZADENXX"
APP_SUBTITLE = "ACCOUNT MANAGER"
APP_VERSION  = "v1.0.0"

# ── Strict monochrome colour tokens ───────────────────────────────────────────
#
#   Nothing outside this set should appear in the UI.
#   Colour roles:
#       C_BLACK / C_DARK / C_SURFACE / C_CARD  →  backgrounds (darkest→lightest)
#       C_BORDER / C_BORDER_HI                 →  dividers and input rings
#       C_WHITE / C_GREY_LT / C_GREY_MID / C_GREY_DK → foregrounds (bright→dim)
#       C_ERROR                                →  validation failure label
#
C_BLACK     = "#000000"
C_DARK      = "#090909"
C_SURFACE   = "#111111"
C_CARD      = "#161616"
C_BORDER    = "#242424"
C_BORDER_HI = "#3E3E3E"
C_WHITE     = "#FFFFFF"
C_GREY_LT   = "#CCCCCC"
C_GREY_MID  = "#888888"
C_GREY_DK   = "#454545"
C_ERROR     = "#EE3333"


# ══════════════════════════════════════════════════════════════════════════════
# §2  PLATFORM-AGNOSTIC STORAGE LAYER
# ══════════════════════════════════════════════════════════════════════════════
#
#   All file I/O goes through these helpers so that flet build can replace the
#   path root with the correct sandboxed directory on Android / iOS without
#   touching any other part of the code.
#
#   Directory resolution priority:
#       1. ZADENXX_DATA  env var      (CI / Docker override)
#       2. ANDROID_DATA  env var      (flet build Android sandbox)
#       3. ~/Library/…               (macOS / iOS)
#       4. ~/.zadenxx                (Windows / Linux)
#

def get_data_dir() -> Path:
    """Return the platform-appropriate writable data directory."""
    if env := os.environ.get("ZADENXX_DATA"):
        return Path(env)

    sys = platform.system()

    if sys == "Android":
        # flet build sets ANDROID_DATA to the app's private files directory
        android_root = os.environ.get("ANDROID_DATA", "/data/data/com.zadenxx.manager/files")
        return Path(android_root) / "zadenxx"

    if sys == "Darwin":
        # Covers macOS desktop and iOS (flet build maps home correctly on device)
        return Path.home() / "Library" / "Application Support" / "zadenxx"

    # Windows + Linux
    return Path.home() / ".zadenxx"


def load_accounts(data_dir: Path) -> list[dict]:
    """
    Read accounts.json from *data_dir*.
    Returns the parsed list, or seeds a set of demo accounts on first launch.
    """
    path = data_dir / "accounts.json"
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh).get("accounts", [])
        except (json.JSONDecodeError, OSError):
            pass                    # corrupt file → fall through to seed
    return _seed_accounts()


def save_accounts(data_dir: Path, accounts: list[dict]) -> None:
    """Persist *accounts* to disk, creating the directory tree if needed."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "accounts.json"
    payload = {
        "accounts":   accounts,
        "saved_at":   datetime.now().isoformat(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _seed_accounts() -> list[dict]:
    """Return demo accounts for a fresh install."""
    return [
        {
            "id":       str(uuid.uuid4()),
            "name":     "Alpha Instance",
            "platform": "Windows",
            "status":   "active",
            "last_run": "2025-06-12T14:32:00",
            "notes":    "Primary production account",
        },
        {
            "id":       str(uuid.uuid4()),
            "name":     "Beta Instance",
            "platform": "Android",
            "status":   "idle",
            "last_run": "2025-06-10T09:11:00",
            "notes":    "Mobile test environment",
        },
        {
            "id":       str(uuid.uuid4()),
            "name":     "Gamma Instance",
            "platform": "iOS",
            "status":   "offline",
            "last_run": "2025-06-01T22:55:00",
            "notes":    "Staging deployment",
        },
    ]


# ══════════════════════════════════════════════════════════════════════════════
# §3  REUSABLE UI COMPONENTS
# ══════════════════════════════════════════════════════════════════════════════

def _status_pill(status: str) -> ft.Container:
    """
    Tiny bordered pill showing a coloured LED dot + status label.

    active  → white    idle → mid-grey    offline → dark-grey
    """
    palette: dict[str, str] = {
        "active":  C_WHITE,
        "idle":    C_GREY_MID,
        "offline": C_GREY_DK,
    }
    colour = palette.get(status.lower(), C_GREY_DK)

    return ft.Container(
        content=ft.Row(
            controls=[
                # LED dot
                ft.Container(width=5, height=5, border_radius=3, bgcolor=colour),
                ft.Text(
                    status.upper(),
                    size=8,
                    color=colour,
                    weight=ft.FontWeight.W_700,
                    letter_spacing=1.8,
                ),
            ],
            spacing=5,
            tight=True,
        ),
        padding=ft.padding.symmetric(horizontal=9, vertical=4),
        border=ft.border.all(1, colour),
        border_radius=3,
        bgcolor=C_SURFACE,
    )


def _stat_col(label: str, value: str) -> ft.Column:
    """Vertical numeric stat block used in the dashboard header bar."""
    return ft.Column(
        controls=[
            ft.Text(value, size=22, color=C_WHITE, weight=ft.FontWeight.W_800),
            ft.Text(label, size=7,  color=C_GREY_MID, letter_spacing=2.0),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=1,
    )


def _divider_v(height: int = 28) -> ft.Container:
    """1-px vertical separator for the stats bar."""
    return ft.Container(width=1, height=height, bgcolor=C_BORDER)


def _text_field(hint: str, password: bool = False) -> ft.TextField:
    """
    Factory for styled TextField controls.
    Keeps consistent border / text / hint styling across auth + dialog fields.
    """
    return ft.TextField(
        hint_text=hint,
        password=password,
        can_reveal_password=password,
        bgcolor=C_SURFACE,
        border_color=C_BORDER_HI,
        focused_border_color=C_WHITE,
        cursor_color=C_WHITE,
        text_style=ft.TextStyle(color=C_WHITE, size=13, letter_spacing=1.4),
        hint_style=ft.TextStyle(color=C_GREY_DK, size=10, letter_spacing=2.0),
        border_radius=4,
        content_padding=ft.padding.symmetric(horizontal=16, vertical=14),
    )


def _account_card(acc: dict, on_launch) -> ft.Container:
    """
    Full-detail account card.

    Sections
    ────────
    [Account name]  ·  [Status pill]
    ──────────────────────────────────
    Platform   |   Last Run
    Notes (italic, dim)
    [▶ LAUNCH INSTANCE] button
    """
    # Format last-run timestamp gracefully
    try:
        lr_str = datetime.fromisoformat(acc["last_run"]).strftime("%d %b %Y · %H:%M")
    except (KeyError, ValueError):
        lr_str = "—"

    def _on_launch_click(e):
        on_launch(acc)

    return ft.Container(
        content=ft.Column(
            controls=[
                # ── Row 1: name + pill ──────────────────────────────────
                ft.Row(
                    controls=[
                        ft.Text(
                            acc.get("name", "Unnamed").upper(),
                            size=12,
                            color=C_WHITE,
                            weight=ft.FontWeight.W_700,
                            letter_spacing=1.4,
                            expand=True,
                        ),
                        _status_pill(acc.get("status", "offline")),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),

                # ── Divider ─────────────────────────────────────────────
                ft.Divider(height=16, color=C_BORDER),

                # ── Row 2: Platform / Last Run ───────────────────────────
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text("PLATFORM", size=7, color=C_GREY_MID, letter_spacing=1.8),
                                ft.Text(acc.get("platform", "—"), size=11, color=C_GREY_LT),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.Column(
                            controls=[
                                ft.Text("LAST RUN", size=7, color=C_GREY_MID, letter_spacing=1.8),
                                ft.Text(lr_str, size=11, color=C_GREY_LT),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                    ],
                ),

                # ── Row 3: Notes ─────────────────────────────────────────
                ft.Container(
                    content=ft.Text(
                        acc.get("notes", ""),
                        size=10,
                        color=C_GREY_DK,
                        italic=True,
                    ),
                    margin=ft.margin.only(top=2),
                ),

                ft.Container(height=12),

                # ── Row 4: Launch button ─────────────────────────────────
                ft.ElevatedButton(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.icons.PLAY_ARROW_ROUNDED, size=13, color=C_BLACK),
                            ft.Text(
                                "LAUNCH INSTANCE",
                                size=9,
                                color=C_BLACK,
                                weight=ft.FontWeight.W_700,
                                letter_spacing=1.8,
                            ),
                        ],
                        tight=True,
                        spacing=6,
                    ),
                    on_click=_on_launch_click,
                    bgcolor=C_WHITE,
                    elevation=0,
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=3),
                        padding=ft.padding.symmetric(vertical=10, horizontal=14),
                    ),
                ),
            ],
            spacing=4,
        ),
        padding=ft.padding.all(18),
        border=ft.border.all(1, C_BORDER),
        border_radius=6,
        bgcolor=C_CARD,
    )


# ══════════════════════════════════════════════════════════════════════════════
# §4  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def main(page: ft.Page) -> None:
    """
    Flet entry point — the entire app lives here.

    State machine
    ─────────────
        boot  ──►  AUTH_VIEW
        valid credentials  ──►  DASHBOARD_VIEW
        sign out           ──►  AUTH_VIEW

    All views are assembled into *body_slot*, which is a child of *root_bg*.
    *root_bg* carries the opacity property that the transparency slider drives.
    """

    # ── Page-level configuration ───────────────────────────────────────────────
    page.title             = "Zadenxx Account Manager"
    page.theme_mode        = ft.ThemeMode.DARK
    page.bgcolor           = ft.colors.BLACK
    # window_bgcolor = TRANSPARENT arms the OS compositor so that when
    # root_bg.opacity < 1.0 the desktop background shows through on Windows 11.
    page.window_bgcolor    = ft.colors.TRANSPARENT
    page.window_width      = 980
    page.window_height     = 720
    page.window_min_width  = 600
    page.window_min_height = 500
    page.padding           = 0
    page.spacing           = 0

    # ── Storage initialisation ─────────────────────────────────────────────────
    data_dir: Path          = get_data_dir()
    accounts: list[dict]    = load_accounts(data_dir)
    save_accounts(data_dir, accounts)   # flush seeds to disk on first run

    # ── Shared mutable state ───────────────────────────────────────────────────
    # Using a dict rather than a bare float so the closure captures the
    # reference and can mutate the value without rebinding the name.
    state: dict = {"bg_opacity": 1.0}

    # ── Root background container (opacity target) ─────────────────────────────
    # Defined early so the slider callback can reference it.
    root_bg = ft.Container(
        bgcolor=C_BLACK,
        opacity=state["bg_opacity"],
        expand=True,
    )

    # ── Body slot — content is swapped by the router ───────────────────────────
    body_slot = ft.Container(expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # §4.1  TRANSPARENCY SLIDER  (persistent top strip)
    # ══════════════════════════════════════════════════════════════════════════

    _opacity_label = ft.Text(
        "1.0",
        size=9,
        color=C_GREY_MID,
        weight=ft.FontWeight.W_600,
        letter_spacing=1.0,
        width=28,
        text_align=ft.TextAlign.RIGHT,
    )

    def _on_slider_change(e: ft.ControlEvent) -> None:
        """
        Fires on every slider tick.
        Updates root_bg.opacity in real time for live window transparency.
        """
        val = round(float(e.control.value), 1)
        state["bg_opacity"] = val
        _opacity_label.value = f"{val:.1f}"
        root_bg.opacity = val
        _opacity_label.update()
        root_bg.update()

    slider_bar = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.icons.CONTRAST_ROUNDED, size=11, color=C_GREY_DK),
                ft.Slider(
                    min=0.2,
                    max=1.0,
                    value=1.0,
                    divisions=8,            # steps: 0.2 0.3 0.4 … 1.0
                    on_change=_on_slider_change,
                    active_color=C_WHITE,
                    inactive_color=C_BORDER,
                    thumb_color=C_WHITE,
                    expand=True,
                ),
                _opacity_label,
                ft.Text("OPACITY", size=8, color=C_GREY_DK, letter_spacing=1.5),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.symmetric(horizontal=16, vertical=4),
        bgcolor=C_DARK,
        border=ft.border.only(bottom=ft.BorderSide(1, C_BORDER)),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # §4.2  AUTH VIEW
    # ══════════════════════════════════════════════════════════════════════════

    f_user = _text_field("USERNAME")
    f_pass = _text_field("PASSWORD", password=True)

    err_label = ft.Text(
        value="",
        size=9,
        color=C_ERROR,
        letter_spacing=1.0,
        visible=False,
    )

    def attempt_login(e: ft.ControlEvent | None = None) -> None:
        """
        Validates credentials against hardcoded owner values.
        Correct  → router transitions to DASHBOARD_VIEW.
        Wrong    → error label surfaces, password field clears.
        """
        u = (f_user.value or "").strip()
        p = (f_pass.value or "").strip()

        if u == OWNER_USERNAME and p == OWNER_PASSWORD:
            err_label.visible = False
            _show_dashboard()           # ← state transition
        else:
            err_label.value   = "INVALID CREDENTIALS — ACCESS DENIED"
            err_label.visible = True
            f_pass.value      = ""
            page.update()

    # Pressing Enter inside the password field submits the form
    f_pass.on_submit = attempt_login

    auth_view = ft.Container(
        content=ft.Column(
            controls=[
                ft.Container(expand=True),      # top flex spacer

                # ── Wordmark block ────────────────────────────────────────
                ft.Column(
                    controls=[
                        ft.Text(
                            APP_TITLE,
                            size=52,
                            color=C_WHITE,
                            weight=ft.FontWeight.W_900,
                            letter_spacing=16,
                        ),
                        ft.Text(
                            APP_SUBTITLE,
                            size=9,
                            color=C_GREY_MID,
                            letter_spacing=5,
                        ),
                        ft.Container(height=6),
                        ft.Container(width=60, height=1, bgcolor=C_BORDER_HI),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                ),

                ft.Container(height=48),

                # ── Login form ────────────────────────────────────────────
                ft.Container(
                    content=ft.Column(
                        controls=[
                            f_user,
                            ft.Container(height=10),
                            f_pass,
                            ft.Container(height=8),
                            err_label,
                            ft.Container(height=20),
                            # Primary CTA
                            ft.ElevatedButton(
                                text="AUTHENTICATE",
                                on_click=attempt_login,
                                bgcolor=C_WHITE,
                                color=C_BLACK,
                                elevation=0,
                                width=340,
                                height=48,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=4),
                                    text_style=ft.TextStyle(
                                        size=11,
                                        weight=ft.FontWeight.W_700,
                                        letter_spacing=2.8,
                                    ),
                                ),
                            ),
                        ],
                        spacing=0,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    width=340,
                ),

                ft.Container(expand=True),      # bottom flex spacer

                # ── Version watermark ─────────────────────────────────────
                ft.Text(APP_VERSION, size=8, color=C_GREY_DK, letter_spacing=2),
                ft.Container(height=28),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        ),
        expand=True,
        padding=ft.padding.symmetric(horizontal=40),
    )

    # ══════════════════════════════════════════════════════════════════════════
    # §4.3  ADD-ACCOUNT DIALOG
    # ══════════════════════════════════════════════════════════════════════════
    #
    #   Lives in page.overlay so it floats above both auth and dashboard views.
    #   The dialog is not rebuilt on each dashboard refresh — only its fields
    #   are cleared.
    #

    _dlg_name     = _text_field("ACCOUNT NAME")
    _dlg_platform = ft.Dropdown(
        hint_text="PLATFORM",
        options=[ft.dropdown.Option(p) for p in ("Windows", "Android", "iOS", "Linux")],
        bgcolor=C_SURFACE,
        border_color=C_BORDER_HI,
        focused_border_color=C_WHITE,
        color=C_WHITE,
        border_radius=4,
        content_padding=ft.padding.symmetric(horizontal=14, vertical=4),
        hint_style=ft.TextStyle(color=C_GREY_DK, size=10, letter_spacing=2.0),
    )
    _dlg_notes    = _text_field("NOTES  (optional)")

    def _close_dialog() -> None:
        add_dialog.open = False
        page.update()

    def _confirm_add(e: ft.ControlEvent | None = None) -> None:
        """Validates name, appends new account, persists, refreshes dashboard."""
        name = (_dlg_name.value or "").strip()
        if not name:
            return

        new_acc: dict = {
            "id":       str(uuid.uuid4()),
            "name":     name,
            "platform": _dlg_platform.value or "Windows",
            "status":   "idle",
            "last_run": datetime.now().isoformat(),
            "notes":    (_dlg_notes.value or "").strip(),
        }
        accounts.append(new_acc)
        save_accounts(data_dir, accounts)

        # Clear fields for next use
        _dlg_name.value = _dlg_notes.value = ""
        _dlg_platform.value = None

        _close_dialog()
        _show_dashboard()       # rebuild grid to include new card

    add_dialog = ft.AlertDialog(
        modal=True,
        bgcolor=C_DARK,
        shape=ft.RoundedRectangleBorder(radius=6),
        title=ft.Text(
            "NEW ACCOUNT",
            color=C_WHITE,
            size=11,
            letter_spacing=2.5,
            weight=ft.FontWeight.W_700,
        ),
        content=ft.Column(
            controls=[
                _dlg_name,
                ft.Container(height=10),
                _dlg_platform,
                ft.Container(height=10),
                _dlg_notes,
            ],
            tight=True,
            width=320,
        ),
        actions=[
            ft.TextButton(
                "CANCEL",
                style=ft.ButtonStyle(color=C_GREY_MID),
                on_click=lambda e: _close_dialog(),
            ),
            ft.TextButton(
                "CREATE",
                style=ft.ButtonStyle(color=C_WHITE),
                on_click=_confirm_add,
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(add_dialog)     # hoisted above all routed views

    # ══════════════════════════════════════════════════════════════════════════
    # §4.4  DASHBOARD VIEW BUILDER
    # ══════════════════════════════════════════════════════════════════════════

    def _on_launch_instance(acc: dict) -> None:
        """
        Called when the user taps "Launch Instance" on any account card.

        Production behaviour: spawn a subprocess, open a socket, call an API.
        This implementation surfaces a confirmation snack bar as a placeholder.
        """
        page.snack_bar = ft.SnackBar(
            content=ft.Text(
                f"▶  LAUNCHING  {acc['name'].upper()}",
                color=C_WHITE,
                size=10,
                letter_spacing=1.8,
            ),
            bgcolor=C_SURFACE,
            duration=3000,
        )
        page.snack_bar.open = True
        page.update()

    def _build_accounts_grid() -> ft.Control:
        """
        Renders account cards in a responsive grid.
        Column distribution:  xs=1  sm=2  md=3
        Falls back to an empty-state message if the list is empty.
        """
        if not accounts:
            return ft.Container(
                content=ft.Text(
                    "NO ACCOUNTS FOUND — TAP + TO ADD ONE",
                    size=11,
                    color=C_GREY_DK,
                    letter_spacing=2,
                ),
                alignment=ft.alignment.center,
                expand=True,
                height=180,
            )

        cards = [_account_card(acc, _on_launch_instance) for acc in accounts]

        # ResponsiveRow adjusts column widths based on available viewport width.
        # On Android/iOS a xs=12 (full-width) single-column layout is used.
        return ft.ResponsiveRow(
            controls=[
                ft.Column(
                    controls=[card],
                    col={"xs": 12, "sm": 6, "md": 4},
                )
                for card in cards
            ],
            run_spacing=12,
            spacing=12,
        )

    def _build_dashboard() -> ft.Container:
        """
        Assembles the full dashboard view:
            [Topbar: wordmark + action icons]
            [Stats bar: totals by status]
            [Scrollable account cards grid]
        """
        # Compute summary counts for the stats bar
        total   = len(accounts)
        active  = sum(1 for a in accounts if a.get("status") == "active")
        idle    = sum(1 for a in accounts if a.get("status") == "idle")
        offline = sum(1 for a in accounts if a.get("status") == "offline")

        return ft.Container(
            content=ft.Column(
                controls=[

                    # ── Topbar ────────────────────────────────────────────
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                # Left: wordmark
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            APP_TITLE,
                                            size=20,
                                            color=C_WHITE,
                                            weight=ft.FontWeight.W_900,
                                            letter_spacing=6,
                                        ),
                                        ft.Text(
                                            APP_SUBTITLE,
                                            size=8,
                                            color=C_GREY_MID,
                                            letter_spacing=3,
                                        ),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                # Right: icon actions
                                ft.IconButton(
                                    icon=ft.icons.ADD_CIRCLE_OUTLINE_ROUNDED,
                                    icon_color=C_WHITE,
                                    icon_size=20,
                                    tooltip="Add account",
                                    on_click=lambda e: _open_add_dialog(),
                                ),
                                ft.IconButton(
                                    icon=ft.icons.LOGOUT_ROUNDED,
                                    icon_color=C_GREY_MID,
                                    icon_size=18,
                                    tooltip="Sign out",
                                    on_click=lambda e: _show_auth(),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.padding.symmetric(horizontal=24, vertical=14),
                        border=ft.border.only(bottom=ft.BorderSide(1, C_BORDER)),
                    ),

                    # ── Stats bar ─────────────────────────────────────────
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                _stat_col("TOTAL",   str(total)),
                                _divider_v(),
                                _stat_col("ACTIVE",  str(active)),
                                _divider_v(),
                                _stat_col("IDLE",    str(idle)),
                                _divider_v(),
                                _stat_col("OFFLINE", str(offline)),
                            ],
                            spacing=20,
                        ),
                        padding=ft.padding.symmetric(horizontal=24, vertical=10),
                        border=ft.border.only(bottom=ft.BorderSide(1, C_BORDER)),
                    ),

                    # ── Cards grid (scrollable) ────────────────────────────
                    ft.Container(
                        content=ft.Column(
                            controls=[_build_accounts_grid()],
                            scroll=ft.ScrollMode.AUTO,
                            expand=True,
                        ),
                        padding=ft.padding.all(20),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
        )

    def _open_add_dialog() -> None:
        add_dialog.open = True
        page.update()

    # ══════════════════════════════════════════════════════════════════════════
    # §4.5  STATE ROUTER
    # ══════════════════════════════════════════════════════════════════════════
    #
    #   Two named transitions.  Each clears any relevant stale state before
    #   injecting a new control tree into body_slot, then calls page.update().
    #

    def _show_auth() -> None:
        """Reset to the authentication view (used on boot and after sign-out)."""
        f_user.value      = ""
        f_pass.value      = ""
        err_label.value   = ""
        err_label.visible = False
        body_slot.content = auth_view
        page.update()

    def _show_dashboard() -> None:
        """Build a fresh dashboard and inject it as the active view."""
        body_slot.content = _build_dashboard()
        page.update()

    # ══════════════════════════════════════════════════════════════════════════
    # §4.6  ROOT LAYOUT ASSEMBLY
    # ══════════════════════════════════════════════════════════════════════════
    #
    #   root_bg
    #     └── Column
    #           ├── slider_bar   (always visible: transparency control strip)
    #           └── body_slot    (swapped by router: auth_view or dashboard)
    #

    root_bg.content = ft.Column(
        controls=[
            slider_bar,     # §4.1  pinned to top
            body_slot,      # §4.5  routed view
        ],
        spacing=0,
        expand=True,
    )

    page.add(root_bg)

    # ── Boot: start on the auth screen ────────────────────────────────────────
    _show_auth()


# ══════════════════════════════════════════════════════════════════════════════
# §5  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
#
#   Run locally:
#       python zadenxx_account_manager.py
#
#   Run with hot-reload in VS Code:
#       python -m flet run zadenxx_account_manager.py
#
#   Build for Android:
#       flet build apk
#
#   Build for iOS (macOS host only):
#       flet build ipa
#

if __name__ == "__main__":
    ft.app(
        target=main,
        # assets_dir="assets",       # uncomment once you add fonts / icons
        # view=ft.WEB_BROWSER,       # uncomment to open in browser instead
    )
