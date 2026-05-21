#!/usr/bin/env python3

import os
from pathlib import Path
from selectors import DefaultSelector, EVENT_READ
from evdev import InputDevice, UInput, ecodes as e

PROJECT_ROOT = Path(__file__).resolve().parent
CLOSE_SIGNAL_PATH = Path(
    os.getenv("ARCADE_CLOSE_SIGNAL_PATH", PROJECT_ROOT / "logs" / "close_game.signal")
)
GAME_RUNNING_FLAG_PATH = Path(
    os.getenv("ARCADE_GAME_RUNNING_FLAG_PATH", PROJECT_ROOT / "logs" / "game_running.flag")
)

P1_DEVICE = "/dev/input/by-path/platform-xhci-hcd.1-usb-0:1:1.0-event-joystick"
P2_DEVICE = "/dev/input/by-path/platform-xhci-hcd.0-usb-0:1:1.0-event-joystick"
# Player 1: left side of keyboard
# Movement: WASD
P2_KEYMAP = {
    e.BTN_SOUTH: e.KEY_Q,
    e.BTN_EAST: e.KEY_E,
    e.BTN_NORTH: e.KEY_R,
    e.BTN_WEST: e.KEY_F,
    e.BTN_TL: e.KEY_Z,
    e.BTN_TR: e.KEY_X,
    e.BTN_SELECT: e.KEY_1,
    e.BTN_START: e.KEY_2,

    e.BTN_TRIGGER: e.KEY_Q,
    e.BTN_THUMB: e.KEY_E,
    e.BTN_THUMB2: e.KEY_R,
    e.BTN_TOP: e.KEY_F,
    e.BTN_TOP2: e.KEY_Z,
    e.BTN_PINKIE: e.KEY_X,
    e.BTN_BASE: e.KEY_2,
    e.BTN_BASE2: e.KEY_1,
}

P2_AXISMAP = {
    e.ABS_X: (e.KEY_A, e.KEY_D),
    e.ABS_Y: (e.KEY_W, e.KEY_S),
    e.ABS_HAT0X: (e.KEY_A, e.KEY_D),
    e.ABS_HAT0Y: (e.KEY_W, e.KEY_S),
}

# Player 2: right side of keyboard
# Movement: arrow keys
# Buttons: U I O P J K L Enter
P1_KEYMAP = {
    e.BTN_SOUTH: e.KEY_U,
    e.BTN_EAST: e.KEY_I,
    e.BTN_NORTH: e.KEY_O,
    e.BTN_WEST: e.KEY_P,
    e.BTN_TL: e.KEY_J,
    e.BTN_TR: e.KEY_K,
    e.BTN_SELECT: e.KEY_L,
    e.BTN_START: e.KEY_ENTER,

    e.BTN_TRIGGER: e.KEY_U,
    e.BTN_THUMB: e.KEY_I,
    e.BTN_THUMB2: e.KEY_O,
    e.BTN_TOP: e.KEY_P,
    e.BTN_TOP2: e.KEY_J,
    e.BTN_PINKIE: e.KEY_K,
    e.BTN_BASE: e.KEY_ENTER,
    e.BTN_BASE2: e.KEY_L,
}

P1_AXISMAP = {
    e.ABS_X: (e.KEY_LEFT, e.KEY_RIGHT),
    e.ABS_Y: (e.KEY_UP, e.KEY_DOWN),
    e.ABS_HAT0X: (e.KEY_LEFT, e.KEY_RIGHT),
    e.ABS_HAT0Y: (e.KEY_UP, e.KEY_DOWN),
}

pressed_keys = set()
suppressed_keys = set()


def key_name(code):
    name = e.KEY.get(code, str(code))
    if isinstance(name, list):
        return name[0]
    return name


def emit_key(ui, key, down):
    if down and key not in pressed_keys:
        ui.write(e.EV_KEY, key, 1)
        ui.syn()
        pressed_keys.add(key)
        print(f"DOWN {key_name(key)}")

    elif not down and key in pressed_keys:
        ui.write(e.EV_KEY, key, 0)
        ui.syn()
        pressed_keys.remove(key)
        print(f"UP   {key_name(key)}")


def request_game_close():
    try:
        CLOSE_SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        CLOSE_SIGNAL_PATH.touch(exist_ok=True)
        print("CLOSE current game")
    except OSError as exc:
        print(f"Unable to write close signal: {exc}")


def handle_axis(dev, ui, event, axismap):
    if event.code not in axismap:
        return

    neg_key, pos_key = axismap[event.code]
    info = dev.absinfo(event.code)

    minimum = info.min
    maximum = info.max
    center = (minimum + maximum) / 2
    threshold = (maximum - minimum) * 0.25

    value = event.value

    emit_key(ui, neg_key, value < center - threshold)
    emit_key(ui, pos_key, value > center + threshold)


def handle_event(dev, ui, event, keymap, axismap, player_name):
    if event.type == e.EV_KEY:
        if event.code in keymap:
            target_key = keymap[event.code]

            if event.value == 1:
                if target_key == e.KEY_Z and GAME_RUNNING_FLAG_PATH.exists():
                    suppressed_keys.add(target_key)
                    print(f"{player_name} ", end="")
                    request_game_close()
                    return

                print(player_name, end=" ")
                emit_key(ui, target_key, True)
            elif event.value == 0:
                if target_key in suppressed_keys:
                    suppressed_keys.discard(target_key)
                    return

                print(player_name, end=" ")
                emit_key(ui, target_key, False)

        else:
            print(f"{player_name} unmapped button code: {event.code}")

    elif event.type == e.EV_ABS:
        handle_axis(dev, ui, event, axismap)


def main():
    p1 = InputDevice(P1_DEVICE)
    p2 = InputDevice(P2_DEVICE)

    print(f"Player 1: {p1.path} - {p1.name}")
    print(f"Player 2: {p2.path} - {p2.name}")

    output_keys = set()

    for key in P1_KEYMAP.values():
        output_keys.add(key)
    for key in P2_KEYMAP.values():
        output_keys.add(key)

    for neg_key, pos_key in P1_AXISMAP.values():
        output_keys.add(neg_key)
        output_keys.add(pos_key)

    for neg_key, pos_key in P2_AXISMAP.values():
        output_keys.add(neg_key)
        output_keys.add(pos_key)

    ui = UInput(
        {e.EV_KEY: sorted(output_keys)},
        name="two-player-arcade-virtual-keyboard"
    )

    selector = DefaultSelector()

    selector.register(
        p1,
        EVENT_READ,
        {
            "name": "P1",
            "keymap": P1_KEYMAP,
            "axismap": P1_AXISMAP,
        }
    )

    selector.register(
        p2,
        EVENT_READ,
        {
            "name": "P2",
            "keymap": P2_KEYMAP,
            "axismap": P2_AXISMAP,
        }
    )

    try:
        p1.grab()
        p2.grab()

        print("Both controllers grabbed exclusively.")
        print("Running. Press Ctrl+C to stop.")

        while True:
            for key, _ in selector.select():
                dev = key.fileobj
                config = key.data

                for event in dev.read():
                    handle_event(
                        dev,
                        ui,
                        event,
                        config["keymap"],
                        config["axismap"],
                        config["name"]
                    )

    except KeyboardInterrupt:
        print("\nStopping.")

    finally:
        for key in list(pressed_keys):
            emit_key(ui, key, False)

        try:
            p1.ungrab()
        except Exception:
            pass

        try:
            p2.ungrab()
        except Exception:
            pass

        selector.close()
        ui.close()


if __name__ == "__main__":
    main()