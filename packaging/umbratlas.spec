Name:           umbratlas
Version:        0.3.1
Release:        1%{?dist}
Summary:        Personal AI learning roadmaps for KDE
License:        LicenseRef-Proprietary
URL:            https://github.com/DarolCup/umbratlas
Source0:        umbratlas-%{version}.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.10
Requires:       python3-pyside6

%description
A Qt desktop learning companion with interactive prerequisite graphs,
AI tutoring, practice, local progress and spaced repetition.

%prep
%setup -q

%build

%install
mkdir -p %{buildroot}%{_datadir}/umbratlas/assets %{buildroot}%{_bindir}
cp -r umbratlas %{buildroot}%{_datadir}/umbratlas/
cp assets/umbratlas.png %{buildroot}%{_datadir}/umbratlas/assets/
cat > %{buildroot}%{_bindir}/umbratlas <<'LAUNCHER'
#!/bin/sh
cd /usr/share/umbratlas
exec /usr/bin/python3 -m umbratlas "$@"
LAUNCHER
chmod 755 %{buildroot}%{_bindir}/umbratlas
install -Dm644 assets/io.github.darolcup.umbratlas.desktop %{buildroot}%{_datadir}/applications/io.github.darolcup.umbratlas.desktop
install -Dm644 assets/umbratlas.png %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/io.github.darolcup.umbratlas.png

%files
%doc README.md RELEASE.md
%{_bindir}/umbratlas
%{_datadir}/umbratlas/
%{_datadir}/applications/io.github.darolcup.umbratlas.desktop
%{_datadir}/icons/hicolor/256x256/apps/io.github.darolcup.umbratlas.png

%changelog
* Wed Nov 11 2026 Umbratlas contributors - 0.3.1-1
- Embedded learning pages, optional topics, return sessions and Fedora packaging.
