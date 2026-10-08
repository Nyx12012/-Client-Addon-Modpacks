# Modpacks

A TavernLauncher addon for keeping your mods in packs and turning each pack on where you want it.
Handy when you play on servers that need different mods.

Click **📦 Modpacks** next to 🧪 Mods on the main window.

## How it works

- A pack is a folder of mods in `Modpacks` next to the game, like `Modpacks\QoL\`.
- Each pack is **Off**, **On everywhere**, or **Only on these servers**. Pick the servers from your
  saved and recent servers, or add the one in the IP box.
- Several packs can be on at once. Untick a mod to leave it out of its pack without removing it.
- When the game starts, Modpacks copies the mods of every pack that's on for that server into `Mods`,
  and takes out the ones it put there before that aren't wanted now.
- Loose mods in `Mods` are yours. They always load and Modpacks never touches them.
- A Main Menu launch loads only the packs that are On everywhere.
- The launcher log says which packs loaded, like `Modpacks: QoL, Velaris (12 mods)`.

## The window

- **Packs:** New, Rename, Delete (to the Recycle Bin), Import zip.
- **The selected pack:** its mode, its servers, its mods with their real names, Open folder,
  Add files..., Export zip.
- **Next launch:** exactly what will load for the server in the IP box. Problems show in red.
- **Loose mods:** what's loose in `Mods`. **Make a pack from these...** moves the mods you pick into a
  pack. New packs start On everywhere, so nothing changes until you change the mode.

## Good to know

- If two packs have different files with the same name, a pack mod is also loose in `Mods` as a
  different file, or the same mod would load twice, the game won't start until you fix it. A message
  says what's wrong, and nothing in `Mods` is changed.
- If the game is already running, nothing changes and the launch goes ahead as usual.
- If a file can't be changed, everything is put back the way it was and the launch stops.
- A mod that no pack holds anymore is never deleted. It's kept in `Modpacks\_removed`.
- Config files and `UserData` are shared by all packs.
- TavernEssentials and Circuit's Voice Chat belong to the launcher, so they never go in a pack.
- Export leaves out your mode and servers. An imported pack starts Off, and mods named
  `*.Server.dll` come in unticked because they look like server mods.

## Install

1. Copy the `modpacks` folder into the `addons` folder next to `TavernLauncher - Client.exe`.
2. Open TavernLauncher, click **🧩 Addons**, tick **Modpacks**, click **Save**, then restart the launcher.
