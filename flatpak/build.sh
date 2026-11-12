#!/bin/sh
# Builds the Flatpak of Hamster (io.github.HamsterProjekt.Hamster) from this
# checkout and installs it for the current user.
#
#   flatpak/build.sh                build and install
#   flatpak/build.sh --bundle       also write hamster.flatpak, a single file
#                                   that installs elsewhere with
#                                   `flatpak install --user hamster.flatpak`
#   flatpak/build.sh --no-install   build only (with --bundle: what CI does)
#   flatpak/build.sh --test         also run the tests inside the sandbox
#
# Needs flatpak-builder (`sudo apt install flatpak-builder`, or
# `flatpak install flathub org.flatpak.Builder`) and the Flathub remote, which
# is added for your user if it is missing. The first build downloads the
# GNOME SDK. Nothing is written into this repository except the bundle: the
# build goes to ~/.cache/hamster-flatpak, or to $FLATPAK_WORK if set.
set -e
id=io.github.HamsterProjekt.Hamster
here="$(cd "$(dirname "$0")/.." && pwd)"
manifest="$here/flatpak/$id.yml"
work="${FLATPAK_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/hamster-flatpak}"
bundle_file="$here/hamster.flatpak"

bundle=no
install=yes
test=no
for argument in "$@"; do
    case "$argument" in
        --bundle) bundle=yes ;;
        --no-install) install=no ;;
        --test) test=yes ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown argument: $argument (see --help)" >&2; exit 2 ;;
    esac
done

if command -v flatpak-builder > /dev/null; then
    builder=flatpak-builder
elif flatpak info org.flatpak.Builder > /dev/null 2>&1; then
    builder="flatpak run org.flatpak.Builder"
else
    echo "flatpak-builder is not installed. Either of:" >&2
    echo "    sudo apt install flatpak-builder" >&2
    echo "    flatpak install flathub org.flatpak.Builder" >&2
    exit 1
fi

if ! flatpak remotes --user --columns=name | grep -qx flathub; then
    flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
fi

mkdir -p "$work"
set -- --user --install-deps-from=flathub --force-clean \
    --state-dir="$work/state" --repo="$work/repo"
if [ "$install" = yes ]; then
    set -- "$@" --install
fi
$builder "$@" "$work/build" "$manifest"

if [ "$test" = yes ]; then
    # from the top of the checkout, where the tests are
    (cd "$here" && dbus-run-session -- $builder --run --state-dir="$work/state" \
        "$work/build" "$manifest" python3 -m unittest)
fi

if [ "$bundle" = yes ]; then
    flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
        "$work/repo" "$bundle_file" "$id"
    echo "Bundle written to $bundle_file"
fi
if [ "$install" = yes ]; then
    echo
    echo "Installed. Start it with:   flatpak run $id"
fi
