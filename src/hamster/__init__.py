import gi
try:
    gi.require_version('Gtk', '3.0')  # noqa: E402
    gi.require_version('PangoCairo', '1.0')  # noqa: E402
    # for some reason performance is improved by importing Gtk early
    from gi.repository import Gtk as gtk
except (ImportError, ValueError):
    # The service and the storage work without GTK (e.g. with the Qt windows).
    gtk = None

from hamster.lib import default_logger
from hamster.version import get_version


logger = default_logger(__name__)

(__version__, installed) = get_version()

# cleanup namespace
del get_version
del default_logger
del gtk  # performance is retained
