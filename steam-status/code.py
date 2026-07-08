import os
import alarm
import board
import time
import terminalio
import displayio
import adafruit_imageload
from adafruit_magtag.magtag import MagTag
from adafruit_display_text import label
from adafruit_bitmap_font import bitmap_font

# jpegio ships in the MagTag firmware; guard the import so the fallback
# path still works if you're on an old build without it.
try:
    import jpegio
    _HAVE_JPEGIO = True
except ImportError:
    _HAVE_JPEGIO = False

# --- config ---------------------------------------------------------------
ROOT = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
# Steam CDN art keyed purely by appid -- no API key, no extra API call.
CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"
CAPSULE = "capsule_231x87.jpg"          # 231x87 wide banner
CAPSULE_W, CAPSULE_H = 231, 87
CAPSULE_Y = 4                            # banner top margin
TEXT_MARGIN = 8                          # px kept clear on each side of text

_api_key = os.getenv("STEAM_API_KEY")
_steam_id = os.getenv("STEAM_USER_ID")
_NOT_GAMING = "Not gaming"
REFRESH_SECONDS = 60                     # deep-sleep interval between polls
FALLBACK_BMP = "/bmps/steam.bmp"

COLORS = {"black": 0x000000}

magtag = MagTag()
DISPLAY_W = magtag.display.width          # 296
DISPLAY_H = magtag.display.height         # 128
_decoder = jpegio.JpegDecoder() if _HAVE_JPEGIO else None

# Compact bitmap font for crisp small text; fall back to the built-in font
# if the file is missing so the display still works.
try:
    FONT = bitmap_font.load_font("/fonts/ter-u12n.pcf")
except Exception as err:
    print("font load failed, using terminalio:", err)
    FONT = terminalio.FONT


# --- steam api ------------------------------------------------------------
def get_data(api_key, steam_id):
    url = f"{ROOT}?key={api_key}&steamids={steam_id}"
    r = magtag.network.fetch(url)
    data = r.json()
    r.close()
    return data


def format_data(data):
    """Return (text, gameid). gameid is None when not in a game."""
    resp = data.get("response", {}).get("players")
    if not resp:
        return _NOT_GAMING, None
    player = resp[0]
    persona = player.get("personaname") or "Unknown"
    game = player.get("gameextrainfo")
    gameid = player.get("gameid")
    if game:
        return f"{persona} is playing {game}", gameid
    return _NOT_GAMING, None


# --- icon loading ---------------------------------------------------------
def capsule_tilegrid(gameid, x, y):
    """Fetch the game's capsule JPEG, decode it in RAM, and return a TileGrid
    whose ColorConverter dithers the color image down to the e-ink grays."""
    url = f"{CDN}/{gameid}/{CAPSULE}"
    magtag.network.connect()
    r = magtag.network.fetch(url)
    try:
        jpeg = r.content
    finally:
        r.close()

    w, h = _decoder.open(jpeg)
    bitmap = displayio.Bitmap(w, h, 65536)   # 16-bit RGB565 target
    _decoder.decode(bitmap)
    shader = displayio.ColorConverter(
        input_colorspace=displayio.Colorspace.RGB565_SWAPPED,
        dither=True,
    )
    return displayio.TileGrid(bitmap, pixel_shader=shader, x=x, y=y)


def fallback_tilegrid():
    bmp, pal = adafruit_imageload.load(FALLBACK_BMP)
    x = (DISPLAY_W - bmp.width) // 2          # center on the panel
    return displayio.TileGrid(bmp, pixel_shader=pal, x=x, y=CAPSULE_Y)


def icon_tilegrid(gameid):
    """Best-effort game icon: live capsule, else bundled fallback, else None."""
    if gameid and _HAVE_JPEGIO:
        try:
            x = (DISPLAY_W - CAPSULE_W) // 2
            return capsule_tilegrid(gameid, x=x, y=CAPSULE_Y)
        except Exception as err:          # network/decode/memory -> fall back
            print("capsule fetch failed:", err)
    try:
        return fallback_tilegrid()
    except Exception as err:
        print("fallback load failed:", err)
        return None


# --- drawing --------------------------------------------------------------
def fit_label(text, max_w):
    """One-line Label sized to fit max_w: scale 2 if it fits, else scale 1,
    else truncate with an ellipsis. Measures the font's actual rendered width,
    so it works for monospace or proportional fonts."""
    lbl = label.Label(FONT, text=text, color=COLORS["black"])
    base_w = lbl.bounding_box[2]                     # unscaled pixel width
    if base_w * 2 <= max_w:
        lbl.scale = 2
    elif base_w > max_w:
        per_char = base_w / max(1, len(text))        # px/char (exact for monospace)
        keep = max(1, int(max_w / per_char) - 3)     # leave room for the ellipsis
        lbl.text = text[:keep] + "..."
    return lbl


def draw(text, gameid):
    root_group = magtag.graphics.root_group

    icon = icon_tilegrid(gameid)
    if icon is not None:
        root_group.append(icon)

    # One line below the banner, auto-fit to the panel width.
    banner_bottom = CAPSULE_Y + CAPSULE_H
    playing = fit_label(text, DISPLAY_W - 2 * TEXT_MARGIN)
    playing.anchor_point = (0.5, 0.5)                              # centered
    playing.anchored_position = (DISPLAY_W // 2, (banner_bottom + DISPLAY_H) // 2)
    root_group.append(playing)

    time.sleep(magtag.display.time_to_refresh + 1)
    magtag.display.refresh()
    time.sleep(magtag.display.time_to_refresh + 1)


# --- main -----------------------------------------------------------------
# LIVE path (uncomment to poll the real Steam API -- costs one API call/wake):
# text, gameid = format_data(get_data(_api_key, _steam_id))

# TEST path (no API call): exercises the capsule fetch + render pipeline.
gameid = 646570                                        # Slay the Spire
text = "You is playing Slay the Spire"

draw(text, gameid)

# Free the button pins (the MagTag lib grabs them at startup) so they can be
# used as deep-sleep wake sources.
magtag.peripherals.deinit()

# Wake on the timer OR a press of button A / B (the two leftmost). Buttons are
# active-low, and the ESP32-S2 allows at most two low-level pin alarms, so
# C and D can't wake it. Any wake re-runs this script from the top, which
# re-fetches and redraws -- so a press refreshes immediately.
time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + REFRESH_SECONDS)
btn_a = alarm.pin.PinAlarm(pin=board.BUTTON_A, value=False, pull=True)
btn_b = alarm.pin.PinAlarm(pin=board.BUTTON_B, value=False, pull=True)
alarm.exit_and_deep_sleep_until_alarms(time_alarm, btn_a, btn_b)
