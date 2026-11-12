# Installing the HamsterProjekt fork of Hamster

This fork adds a Markdown storage backend and a service that runs without
GTK (see the [README](README.md)), and has its own D-Bus, settings and
application names (`io.github.HamsterProjekt.Hamster`). The packages of the distributions, the
snap and the Flatpak on Flathub are the upstream Hamster: they do not have
the Markdown backend. To get it, install from this repository in one of
these ways:

| Way | What you get | Root needed |
|---|---|---|
| [With hamster-kde](#with-hamster-kde) (recommended) | The service with the Markdown storage, the Plasma widget and the Qt windows | Only for the packages |
| [The service only, for your user](#the-service-only-for-your-user) | The service with the Markdown storage, for clients of the fork such as hamster-kde | Only for the packages |
| [System-wide with waf](#system-wide-with-waf) | All of Hamster with its GTK windows, installed like upstream; SQLite unless you switch | Yes |

Before any of them, [stop a Hamster that is running](#stop-a-running-hamster).
If you installed the fork before it had its own names, also
[clean up the old names](#upgrading-from-the-old-names).

**SQLite or Markdown?** The service of this fork can store your data in
either. SQLite is the default; the first two ways above set up Markdown.
Any of them can be switched afterwards, see
[Switching between SQLite and Markdown](#switching-between-sqlite-and-markdown).

## Stop a running Hamster

A running service keeps answering until it is stopped, so a newly installed
one would not be used:

```bash
pkill -f hamster-service
```

```bash
pkill -f hamster-windows-service
```

If you have an SQLite database (`~/.local/share/hamster/hamster.db`), back
it up first. The Markdown backend does not read it, and there is no
migration.

## Upgrading from the old names

Until November 2026 the fork used upstream's names: the D-Bus services
`org.gnome.Hamster`, `org.gnome.Hamster.WindowServer` and
`org.gnome.Hamster.GUI`, and the settings schema `org.gnome.hamster`. It now
uses `io.github.HamsterProjekt.Hamster` for all of them. If you installed it
before, do this once after updating the checkout. (The hamster-kde installer
does the first step itself.)

1. Remove the service files and the schema that the earlier install put in
   your home folder. A distribution's Hamster keeps its files under `/usr`,
   so this does not touch it.

   ```bash
   rm -f ~/.local/share/dbus-1/services/org.gnome.Hamster.service ~/.local/share/dbus-1/services/org.gnome.Hamster.WindowServer.service ~/.local/share/dbus-1/services/org.gnome.Hamster.GUI.service ~/.local/share/glib-2.0/schemas/org.gnome.hamster.gschema.xml
   ```

2. Copy your settings, among them the folder of the Markdown files, to the
   new schema:

   ```bash
   dconf dump /org/gnome/hamster/ | dconf load /io/github/HamsterProjekt/Hamster/
   ```

3. Install again in the way you used before.

## With hamster-kde

The installer of [hamster-kde](https://github.com/HamsterProjekt/hamster-kde) sets
up the service of this repository together with the widget and the windows.
Follow its
[INSTALL.md](https://github.com/HamsterProjekt/hamster-kde/blob/main/INSTALL.md);
in short:

```bash
git clone https://github.com/HamsterProjekt/hamster.git ~/projects/hamster
```

```bash
git clone https://github.com/HamsterProjekt/hamster-kde.git ~/projects/hamster-kde
```

```bash
~/projects/hamster-kde/install.sh
```

## The service only, for your user

This runs the service from the checkout, started on demand by D-Bus. It is
what the hamster-kde installer does for the service, by hand.

1. Install what the service needs. GTK is not among it.

   ```bash
   sudo apt install git python3-gi python3-dbus libglib2.0-bin dbus-bin
   ```

   On another distribution: Python 3 with PyGObject and dbus-python,
   `glib-compile-schemas` and `dbus-send`.

2. Get the source. Keep the checkout: the service runs from it.

   ```bash
   git clone https://github.com/HamsterProjekt/hamster.git ~/projects/hamster
   ```

3. Install Hamster's settings schema for your user. The service does not
   start without it, and the schema of an upstream Hamster lacks the
   `markdown-directory` key.

   ```bash
   mkdir -p ~/.local/share/glib-2.0/schemas ~/.local/share/dbus-1/services
   ```

   ```bash
   cp ~/projects/hamster/data/io.github.HamsterProjekt.Hamster.gschema.xml ~/.local/share/glib-2.0/schemas/
   ```

   ```bash
   glib-compile-schemas ~/.local/share/glib-2.0/schemas
   ```

4. Tell D-Bus how to start the service, with the Markdown storage:

   ```bash
   printf '[D-BUS Service]\nName=io.github.HamsterProjekt.Hamster\nExec=/usr/bin/env HAMSTER_STORAGE=markdown %s %s/projects/hamster/src/hamster-service.py\n' "$(command -v python3)" "$HOME" > ~/.local/share/dbus-1/services/io.github.HamsterProjekt.Hamster.service
   ```

   ```bash
   dbus-send --session --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig
   ```

5. Check it. This starts the service and prints its version:

   ```bash
   dbus-send --session --print-reply --dest=io.github.HamsterProjekt.Hamster /io/github/HamsterProjekt/Hamster io.github.HamsterProjekt.Hamster.Version
   ```

   Facts are then written to `~/.local/share/hamster/markdown`; the
   [README](README.md#another-folder-several-computers) says how to use
   another folder.

To update, `git pull` in the checkout and stop the service
(`pkill -f hamster-service`); it starts again with the new code when it is
next asked for.

To uninstall, stop the service and remove what steps 3 and 4 added. Your
Markdown files stay.

```bash
rm ~/.local/share/dbus-1/services/io.github.HamsterProjekt.Hamster.service ~/.local/share/glib-2.0/schemas/io.github.HamsterProjekt.Hamster.gschema.xml
```

```bash
glib-compile-schemas ~/.local/share/glib-2.0/schemas
```

### Without installing anything

To try the service from the checkout in a terminal, with the schema of
step 3 in place:

```bash
HAMSTER_STORAGE=markdown python3 src/hamster-service.py
```

It runs until Ctrl+C, and prints its errors there.

## System-wide with waf

This installs all of Hamster from this repository the way upstream does,
with its GTK windows and the `hamster` command. The service it installs uses the SQLite database.

1. Install the dependencies (Debian and Ubuntu; for openSUSE and Fedora see
   the [README](README.md#dependencies)):

   ```bash
   sudo apt install gettext intltool python3-gi python3-cairo python3-gi-cairo python3-dbus libglib2.0-dev libglib2.0-bin gir1.2-gtk-3.0 gtk-update-icon-cache
   ```

2. Build and install. `waf` is started with `python3` because many systems
   have no `python` command. The parentheses keep the umask of your shell
   as it is.

   ```bash
   python3 ./waf configure build
   ```

   ```bash
   ( umask 0022 && sudo python3 ./waf install; )
   ```

   The prefix is `/usr`; give another one to `configure` with `--prefix`.

3. To use the Markdown storage with it, put a service file for your user in
   front of the installed one:

   ```bash
   mkdir -p ~/.local/share/dbus-1/services
   ```

   ```bash
   printf '[D-BUS Service]\nName=io.github.HamsterProjekt.Hamster\nExec=/usr/bin/env HAMSTER_STORAGE=markdown /usr/libexec/hamster/hamster-service\n' > ~/.local/share/dbus-1/services/io.github.HamsterProjekt.Hamster.service
   ```

   Then stop the running service. Without this file, Hamster works as
   upstream, with SQLite.

To uninstall: `sudo python3 ./waf uninstall` from the same checkout, and
remove the service file of step 3 if you made it.

The build with waf was not tried on Python 3.12 or newer for this fork.
If `configure` fails there, use one of the two other ways; they do not
need waf.

## Switching between SQLite and Markdown

The storage is chosen by `HAMSTER_STORAGE` in the `Exec` line of the
service file that D-Bus uses to start the service:

    ~/.local/share/dbus-1/services/io.github.HamsterProjekt.Hamster.service

With `HAMSTER_STORAGE=markdown` in it, the service uses the Markdown files;
without it, SQLite. The two keep separate data, and neither is converted
into the other, so back up `~/.local/share/hamster/hamster.db` before
experimenting.

**To use SQLite**, remove `/usr/bin/env HAMSTER_STORAGE=markdown ` from the
`Exec` line, so that it reads for instance:

    Exec=/usr/bin/python3 /home/you/projects/hamster/src/hamster-service.py

or, with the system-wide installation, delete the file altogether: the
installed service file then applies, and it uses SQLite.

**To use Markdown**, put it back:

    Exec=/usr/bin/env HAMSTER_STORAGE=markdown /usr/bin/python3 /home/you/projects/hamster/src/hamster-service.py

Either way, stop the running service afterwards; D-Bus starts it again with
the new storage on the next call:

```bash
pkill -f hamster-service
```

To check which storage is used, look at what the service printed when it
started:

```bash
journalctl --user -b | grep 'storage:'
```

With hamster-kde you do not need to edit the file: its installer takes
`--sqlite` or `--markdown` (`./install.sh --sqlite`), and keeps the chosen
storage when run again without an option. hamster-kde works the same on
both storages.

## When something does not work

**The service does not start.** Run it in a terminal, as under
[Without installing anything](#without-installing-anything), and read what
it prints. `Settings schema 'io.github.HamsterProjekt.Hamster' is not installed` or
`No GSettings schemas are installed on the system` means step 3 of the
per-user installation is missing. A missing Python module
means a package is not installed. With the Markdown storage, the service
also refuses to start when its folder cannot be created.

**The old activities still show.** Another Hamster service is answering.
See which program has the name, stop it, and remove the distribution's
package if it keeps coming back:

```bash
busctl --user status io.github.HamsterProjekt.Hamster | grep -E 'PID|CommandLine'
```

**Nothing appears in `~/.local/share/hamster/markdown`.** The service runs
with the SQLite storage: `HAMSTER_STORAGE=markdown` is not in the `Exec`
line of the service file that D-Bus uses, or another folder is set in the
`markdown-directory` setting.
