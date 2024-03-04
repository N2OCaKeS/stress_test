-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA256

Format: 3.0 (quilt)
Source: postgresql-15
Binary: libpq-dev, libpq5, libecpg6, libecpg-dev, libecpg-compat3, libpgtypes3, postgresql-15, postgresql-client-15, postgresql-server-dev-15, postgresql-doc-15, postgresql-plperl-15, postgresql-plpython3-15, postgresql-pltcl-15
Architecture: any all
Version: 15.6-0+deb12u1
Maintainer: Debian PostgreSQL Maintainers <team+postgresql@tracker.debian.org>
Uploaders:  Martin Pitt <mpitt@debian.org>, Peter Eisentraut <petere@debian.org>, Christoph Berg <myon@debian.org>,
Homepage: http://www.postgresql.org/
Standards-Version: 4.5.0
Vcs-Browser: https://salsa.debian.org/postgresql/postgresql
Vcs-Git: https://salsa.debian.org/postgresql/postgresql.git -b 15-bookworm
Testsuite: autopkgtest
Testsuite-Triggers: build-essential, debhelper, fakeroot, hunspell-en-us, iproute2, locales-all, logrotate, netcat-openbsd, perl, procps
Build-Depends: autoconf, bison, clang [!alpha !hppa !hurd-i386 !ia64 !kfreebsd-amd64 !kfreebsd-i386 !m68k !powerpc !riscv64 !s390x !sh4 !sparc64 !x32], debhelper-compat (= 13), dh-exec (>= 0.13~), docbook-xml, docbook-xsl (>= 1.77), dpkg-dev (>= 1.16.1~), flex, gdb <!nocheck>, gettext, libicu-dev, libio-pty-perl <!nocheck>, libipc-run-perl <!nocheck>, libkrb5-dev, libldap2-dev, liblz4-dev, libpam0g-dev | libpam-dev, libperl-dev, libreadline-dev, libselinux1-dev [linux-any], libssl-dev, libsystemd-dev [linux-any], libxml2-dev, libxml2-utils, libxslt1-dev, libzstd-dev (>= 1.4.0) <!pkg.postgresql.nozstd>, llvm-dev [!alpha !hppa !hurd-i386 !ia64 !kfreebsd-amd64 !kfreebsd-i386 !m68k !powerpc !riscv64 !s390x !sh4 !sparc64 !x32], lz4 | liblz4-tool, mawk, perl (>= 5.8), pkg-config, postgresql-common (>= 233~), python3-dev, systemtap-sdt-dev, tcl-dev, uuid-dev, xsltproc, zlib1g-dev | libz-dev, zstd (>= 1.4.0) <!pkg.postgresql.nozstd>
Package-List:
 libecpg-compat3 deb libs optional arch=any
 libecpg-dev deb libdevel optional arch=any
 libecpg6 deb libs optional arch=any
 libpgtypes3 deb libs optional arch=any
 libpq-dev deb libdevel optional arch=any
 libpq5 deb libs optional arch=any
 postgresql-15 deb database optional arch=any
 postgresql-client-15 deb database optional arch=any
 postgresql-doc-15 deb doc optional arch=all profile=!nodoc
 postgresql-plperl-15 deb database optional arch=any
 postgresql-plpython3-15 deb database optional arch=any
 postgresql-pltcl-15 deb database optional arch=any
 postgresql-server-dev-15 deb libdevel optional arch=any
Checksums-Sha1:
 c62fb81e3eccbab523d840a7717c14a0b3a82a02 23093967 postgresql-15_15.6.orig.tar.bz2
 77cb7c2f69c7bdb4e918076ab0db6e1b75296c85 25272 postgresql-15_15.6-0+deb12u1.debian.tar.xz
Checksums-Sha256:
 8455146ed9c69c93a57de954aead0302cafad035c2b242175d6aa1e17ebcb2fb 23093967 postgresql-15_15.6.orig.tar.bz2
 8c82dc4cf12db5c640c527981e83d73c33c1530293cf3314692c82dffbe07ec4 25272 postgresql-15_15.6-0+deb12u1.debian.tar.xz
Files:
 666511aeb53bd4ac029e236e35b42ca8 23093967 postgresql-15_15.6.orig.tar.bz2
 340055ef4345296c8ee246c821ee87e9 25272 postgresql-15_15.6-0+deb12u1.debian.tar.xz

-----BEGIN PGP SIGNATURE-----

iQIzBAEBCAAdFiEEXEj+YVf0kXlZcIfGTFprqxLSp64FAmXMsMUACgkQTFprqxLS
p670Sw//RR3MB13AUWOPziOxAOND2XqxD6jNiYuwCe4eJWV9fUjQxpIOHFbzbrlX
SScgfbg0/QKnDX9/vV8Y+szVwnwly0A2dPoVFGUcgkc0mG06mbbRij0G6li6/JM2
g67B1ZuWK3a8h3JLtmTfztT2kx+1+hdfh65OYNu2NBmHHlKPdksPB4CifHk/91cB
dj1RpSHRfzg02RqhHIsMQFFuP80F+wWp3p0cLzNsfxSxvu7SgBDMdFeRUeASy9Z3
o+LqHUWaRrGohzhTQTg9Sle24txju/QwDvtJzbu5orSbitYj6992Py4cmKQPC+VH
RNaSvKGH9W+aIWhzoNJd4A57WE0M92F/cP6Fgi+4cPGW7Tm5H0PciN80Bm2hfGES
sGd7DqyzfVZATqJ5vxAJI7NuXMOKh3N+Ndr35vZLaQpRu1Q2zDpRfIyJq8NxaANb
GCFlESyvx0opkPjuHpXKXS+bFqx/18KAj8zvvyEVSPE4QSn4bt8pVM/Fou5PtdGw
uTHOaHnJPUtmItoLo8bZNrW/Gy+bip9V391PzJNMyLCw2FGVHGSsy5WQ76j5aqFI
rrqTxBq5OYMlUhZ+CdjAC1IU+cfoRoHGP9h3FHVyUkfaOFhU9Xi36oIHTgKv0yaQ
DFB4yL7UQ7TydNJwx6ECbw8QjxudWj4yes9fK0APJ6Lo+RW/494=
=7ExE
-----END PGP SIGNATURE-----
