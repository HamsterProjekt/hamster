# Security policy

## Reporting a vulnerability

Please don't report security problems in public issues or discussions.

Report them privately instead: open the repository's
[Security tab](https://github.com/HamsterProjekt/hamster/security) and choose
**Report a vulnerability**. Only the maintainers can see the report, and we
can work on a fix and an advisory together there.

Please include:

- what the problem is, and what an attacker could do with it
- the steps to reproduce it, or a proof of concept
- the version (the Flatpak release, or the commit) and how you installed it

We will reply within a week, and keep you informed until it is fixed. Unless
you prefer otherwise, we credit you in the advisory.

## Supported versions

Only the latest [release](https://github.com/HamsterProjekt/hamster/releases)
and the `main` branch get security fixes.

## Scope

This policy covers the code in this repository, for example:

- the Hamster service and its D-Bus interface (`io.github.HamsterProjekt.*`)
- the storage backends: the SQLite database and the Markdown files
- the HTML, TSV, XML and iCal reports
- the Flatpak and the GitHub Actions workflows

Report problems in other projects to them:

- upstream [Project Hamster](https://github.com/projecthamster/hamster), for
  code that this fork hasn't changed
- [waf](https://gitlab.com/ita1024/waf), the bundled build tool in `waf` and
  `waflib/`
- the libraries that Hamster uses, such as GTK and PyGObject
- [hamster-kde](https://github.com/HamsterProjekt/hamster-kde), the KDE
  Plasma widget and windows, which has its own security policy
