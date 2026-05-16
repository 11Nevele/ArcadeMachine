# Game Library

Add one folder per game under this directory.

Each game folder should contain:

```text
games/
  My Game/
    MyGame.jar
    cover.png
    des.txt
```

Rules:

- The launcher expects exactly one `.jar` file in each game folder.
- `cover.png` is shown in the grid and in the detail panel.
- `des.txt` is shown in the right-side description panel.
- If `cover.png` or `des.txt` is missing, the game still appears with a placeholder and a warning.
- If the `.jar` file is missing or there are multiple `.jar` files, that folder is skipped.